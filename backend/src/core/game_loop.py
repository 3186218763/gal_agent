"""
游戏主循环 - 核心游戏逻辑
使用 OpenAI Agents SDK
"""
import asyncio
from typing import Optional
from fastapi import WebSocket

from ..models import GameState, NarrativeBeat, ChapterMetadata
from ..agents import DirectorAgent, CharacterFactory
from ..agents.director import GameContext
from .script_parser import ScriptParser
from .state_manager import StateManager


class GameLoop:
    """游戏主循环，使用 OpenAI Agents SDK"""

    def __init__(
        self,
        state_manager: StateManager,
        script_parser: ScriptParser
    ):
        self.state_manager = state_manager
        self.script_parser = script_parser

        # Agents
        self.director: Optional[DirectorAgent] = None
        self.character_factory: Optional[CharacterFactory] = None
        self.game_context: Optional[GameContext] = None

    async def run(self, session_id: str, websocket: WebSocket):
        """
        运行游戏循环

        Args:
            session_id: 会话 ID
            websocket: WebSocket 连接
        """
        # 获取游戏状态
        game_state = self.state_manager.get_session(session_id)
        if not game_state:
            await websocket.send_json({
                "type": "error",
                "message": f"Session not found: {session_id}"
            })
            return

        # 解析章节
        try:
            metadata, beats = self.script_parser.parse_chapter(game_state.current_chapter)
        except FileNotFoundError as e:
            await websocket.send_json({
                "type": "error",
                "message": str(e)
            })
            return

        # 初始化 agents
        self.director = DirectorAgent()
        self.character_factory = CharacterFactory()
        self.game_context = GameContext()

        # 创建角色 agents
        for character in metadata.characters:
            self.character_factory.create_character(
                character.id,
                character.name,
                character.personality
            )

        # 发送游戏开始事件
        await websocket.send_json({
            "type": "game_start",
            "chapter": metadata.title,
            "session_id": session_id
        })

        # 主循环
        try:
            await self._game_loop(
                websocket,
                game_state,
                metadata,
                beats
            )
        except Exception as e:
            print(f"Error in game loop: {e}")
            await websocket.send_json({
                "type": "error",
                "message": f"Game loop error: {str(e)}"
            })

    async def _game_loop(
        self,
        websocket: WebSocket,
        game_state: GameState,
        metadata: ChapterMetadata,
        beats: list[NarrativeBeat]
    ):
        """内部游戏循环"""

        while game_state.current_beat_index < len(beats):
            beat = beats[game_state.current_beat_index]

            # 1. 执行 beat 内容
            await self._execute_beat(websocket, game_state, beat)

            # 2. 应用 beat 的状态变化
            if beat.flags_to_set:
                self.state_manager.update_flags(game_state.session_id, beat.flags_to_set)
                await websocket.send_json({
                    "type": "state_update",
                    "changes": {
                        "flags": beat.flags_to_set
                    }
                })

            # 3. 更新紧张度 (简化版本)
            game_state.tension_level = self._calculate_tension(game_state, beat)

            # 4. 推进 beat
            self.state_manager.advance_beat(game_state.session_id)

            # 5. 判断是否触发选项
            trigger_decision = await self.director.should_trigger_option(
                game_state,
                beat,
                self.game_context
            )

            if trigger_decision.get('should_trigger', False):
                # 生成并发送选项
                await self._handle_options(websocket, game_state, beat)

            # 6. 检查结局 (简化版本)
            if game_state.current_beat_index >= len(beats) - 1:
                # 章节结束，检查结局
                ending = self._check_ending(game_state, metadata)
                if ending:
                    await websocket.send_json({
                        "type": "ending",
                        "ending_id": ending['id'],
                        "title": ending['title'],
                        "content": ending['content'],
                        "ending_type": ending['type']
                    })
                    return

            # 短暂延迟
            await asyncio.sleep(1)

        # 章节完成
        await websocket.send_json({
            "type": "chapter_complete",
            "chapter": metadata.chapter_id
        })

    async def _execute_beat(
        self,
        websocket: WebSocket,
        game_state: GameState,
        beat: NarrativeBeat
    ):
        """执行单个 beat"""

        if beat.beat_type == "narration":
            # 叙事内容直接发送
            await websocket.send_json({
                "type": "narration",
                "content": beat.content,
                "mood": beat.mood
            })

        elif beat.beat_type == "dialogue":
            # 尝试从内容中提取角色
            character_id = self._extract_character_from_beat(beat)

            if character_id and character_id in game_state.relationships:
                # 让角色 agent 生成对话
                character_agent = self.character_factory.get_character(character_id)
                character_context = self.character_factory.get_context(character_id)

                if character_agent and character_context:
                    try:
                        dialogue = await character_agent.speak(
                            context=beat.content,
                            relationship=game_state.relationships[character_id],
                            character_context=character_context
                        )

                        await websocket.send_json({
                            "type": "dialogue",
                            "character": character_id,
                            "content": dialogue,
                            "mood": beat.mood
                        })
                    except Exception as e:
                        print(f"Error generating dialogue: {e}")
                        # 回退到直接发送原始内容
                        await websocket.send_json({
                            "type": "narration",
                            "content": beat.content,
                            "mood": beat.mood
                        })
                else:
                    await websocket.send_json({
                        "type": "narration",
                        "content": beat.content,
                        "mood": beat.mood
                    })
            else:
                await websocket.send_json({
                    "type": "narration",
                    "content": beat.content,
                    "mood": beat.mood
                })

    async def _handle_options(
        self,
        websocket: WebSocket,
        game_state: GameState,
        beat: NarrativeBeat
    ):
        """处理选项生成和玩家选择"""

        # 生成选项
        try:
            options = await self.director.generate_options(
                game_state,
                beat,
                self.game_context
            )
        except Exception as e:
            print(f"Error generating options: {e}")
            return

        if not options:
            print("No options generated, skipping")
            return

        # 发送选项到前端
        await websocket.send_json({
            "type": "options",
            "options": [
                {
                    "id": f"opt_{i}",
                    "text": opt.text,
                    "preview": opt.narrative_impact
                }
                for i, opt in enumerate(options)
            ]
        })

        # 等待玩家选择
        choice_data = await websocket.receive_json()

        if choice_data.get('type') != 'player_choice':
            print(f"Unexpected message type: {choice_data.get('type')}")
            return

        option_index = choice_data.get('option_index', 0)

        if 0 <= option_index < len(options):
            chosen_option = options[option_index]

            # 应用选择后果
            await self._apply_consequences(
                websocket,
                game_state,
                chosen_option.predicted_consequences
            )

            # 重置选项计数器
            self.state_manager.reset_option_counter(game_state.session_id)

    async def _apply_consequences(
        self,
        websocket: WebSocket,
        game_state: GameState,
        consequences: dict
    ):
        """应用选择后果"""

        # 更新 flags
        flag_changes = consequences.get('flag_changes', {})
        if flag_changes:
            self.state_manager.update_flags(
                game_state.session_id,
                flag_changes
            )

        # 更新关系
        relationship_deltas = consequences.get('relationship_deltas', {})
        if relationship_deltas:
            for char_id, deltas in relationship_deltas.items():
                if char_id in game_state.relationships:
                    self.state_manager.update_relationship(
                        game_state.session_id,
                        char_id,
                        trust_delta=deltas.get('trust', 0),
                        romance_delta=deltas.get('romance', 0)
                    )

        # 发送状态更新
        await websocket.send_json({
            "type": "state_update",
            "changes": {
                "flags": flag_changes,
                "relationships": {
                    char_id: {
                        "trust": game_state.relationships[char_id].trust,
                        "romance": game_state.relationships[char_id].romance
                    }
                    for char_id in game_state.relationships
                }
            }
        })

    def _calculate_tension(self, game_state: GameState, beat: NarrativeBeat) -> int:
        """计算紧张度 (简化版本)"""
        tension = 5  # 基线

        # 长时间对话降低紧张感
        if game_state.dialogue_count_since_last_option > 6:
            tension -= 1

        # 关键 flags 变化提升紧张感
        if len(beat.flags_to_set) > 2:
            tension += 2

        # 选项点提升紧张感
        if beat.has_option_point:
            tension += 2

        return max(1, min(10, tension))

    def _check_ending(self, game_state: GameState, metadata: ChapterMetadata) -> Optional[dict]:
        """检查结局条件 (简化版本)"""
        # 按优先级排序
        sorted_endings = sorted(metadata.endings, key=lambda e: e.priority, reverse=True)

        for ending in sorted_endings:
            if self._evaluate_condition(ending.condition, game_state):
                return {
                    'id': ending.id,
                    'title': ending.title,
                    'content': ending.content,
                    'type': ending.type.value
                }

        return None

    def _evaluate_condition(self, condition: str, game_state: GameState) -> bool:
        """评估结局条件 (简化版本)"""
        try:
            # 构建求值上下文
            context = {
                **game_state.flags,
            }

            # 添加关系值作为变量
            for char_id, rel in game_state.relationships.items():
                context[f"{char_id}_trust"] = rel.trust
                context[f"{char_id}_romance"] = rel.romance

            # 使用 eval 评估条件 (生产环境应该用更安全的方法)
            return eval(condition, {"__builtins__": {}}, context)
        except Exception as e:
            print(f"Error evaluating condition '{condition}': {e}")
            return False

    def _extract_character_from_beat(self, beat: NarrativeBeat) -> Optional[str]:
        """从 beat 中提取角色 ID（简单启发式）"""
        # 从 character_interactions 中提取
        if beat.character_interactions:
            for interaction in beat.character_interactions:
                if 'alice' in interaction.lower() or '艾丽丝' in interaction:
                    return 'alice'
                elif 'bob' in interaction.lower() or '鲍勃' in interaction:
                    return 'bob'

        # 从内容中检测角色名
        content_lower = beat.content.lower()
        if '艾丽丝' in beat.content or 'alice' in content_lower:
            return 'alice'
        elif '鲍勃' in beat.content or 'bob' in content_lower:
            return 'bob'

        return None
