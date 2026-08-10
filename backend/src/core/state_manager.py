"""
状态管理器 - 游戏状态的持久化和查询
"""
from typing import Dict, Optional
import json
from pathlib import Path
from ..models import GameState, Relationship


class StateManager:
    """游戏状态管理器"""

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)
        self.sessions: Dict[str, GameState] = {}

    def create_session(
        self,
        session_id: str,
        chapter_id: str,
        characters: Dict[str, Dict[str, int]]
    ) -> GameState:
        """
        创建新游戏会话

        Args:
            session_id: 会话 ID
            chapter_id: 章节 ID
            characters: 角色初始关系 {char_id: {trust: int, romance: int}}

        Returns:
            游戏状态
        """
        relationships = {}
        for char_id, values in characters.items():
            relationships[char_id] = Relationship(
                trust_level=values.get('trust', 50),
                romance_level=values.get('romance', 0),
                memory_log=[]
            )

        game_state = GameState(
            session_id=session_id,
            current_chapter=chapter_id,
            current_beat_index=0,
            flags={},
            relationships=relationships,
            turns_since_last_option=0,
            tension_level=5
        )

        self.sessions[session_id] = game_state
        self.save_session(session_id)

        return game_state

    def get_session(self, session_id: str) -> Optional[GameState]:
        """获取游戏会话"""
        if session_id in self.sessions:
            return self.sessions[session_id]

        # 尝试从磁盘加载
        return self.load_session(session_id)

    def save_session(self, session_id: str) -> bool:
        """保存游戏会话到磁盘"""
        if session_id not in self.sessions:
            return False

        game_state = self.sessions[session_id]
        file_path = self.data_dir / f"{session_id}.json"

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(game_state.to_dict(), f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"Error saving session {session_id}: {e}")
            return False

    def load_session(self, session_id: str) -> Optional[GameState]:
        """从磁盘加载游戏会话"""
        file_path = self.data_dir / f"{session_id}.json"

        if not file_path.exists():
            return None

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # 重建 Relationship 对象
            relationships = {}
            for char_id, rel_data in data['relationships'].items():
                relationships[char_id] = Relationship(
                    trust_level=rel_data['trust_level'],
                    romance_level=rel_data['romance_level'],
                    memory_log=rel_data['memory_log']
                )

            game_state = GameState(
                session_id=data['session_id'],
                current_chapter=data['current_chapter'],
                current_beat_index=data['current_beat_index'],
                flags=data['flags'],
                relationships=relationships,
                turns_since_last_option=data['turns_since_last_option'],
                tension_level=data['tension_level']
            )

            self.sessions[session_id] = game_state
            return game_state

        except Exception as e:
            print(f"Error loading session {session_id}: {e}")
            return None

    def delete_session(self, session_id: str) -> bool:
        """删除游戏会话"""
        # 从内存删除
        if session_id in self.sessions:
            del self.sessions[session_id]

        # 从磁盘删除
        file_path = self.data_dir / f"{session_id}.json"
        if file_path.exists():
            try:
                file_path.unlink()
                return True
            except Exception as e:
                print(f"Error deleting session {session_id}: {e}")
                return False

        return True

    def update_flags(self, session_id: str, flags: Dict[str, any]) -> bool:
        """更新游戏标记"""
        game_state = self.get_session(session_id)
        if not game_state:
            return False

        game_state.flags.update(flags)
        return self.save_session(session_id)

    def update_relationship(
        self,
        session_id: str,
        character_id: str,
        trust_delta: int = 0,
        romance_delta: int = 0
    ) -> bool:
        """更新角色关系"""
        game_state = self.get_session(session_id)
        if not game_state or character_id not in game_state.relationships:
            return False

        rel = game_state.relationships[character_id]
        rel.trust_level = max(0, min(100, rel.trust_level + trust_delta))
        rel.romance_level = max(0, min(100, rel.romance_level + romance_delta))

        return self.save_session(session_id)

    def advance_beat(self, session_id: str) -> bool:
        """推进到下一个 beat"""
        game_state = self.get_session(session_id)
        if not game_state:
            return False

        game_state.current_beat_index += 1
        game_state.turns_since_last_option += 1

        return self.save_session(session_id)

    def reset_option_counter(self, session_id: str) -> bool:
        """重置选项计数器（玩家做出选择后调用）"""
        game_state = self.get_session(session_id)
        if not game_state:
            return False

        game_state.turns_since_last_option = 0
        return self.save_session(session_id)

    def list_sessions(self) -> list[str]:
        """列出所有会话 ID"""
        session_files = self.data_dir.glob("*.json")
        return [f.stem for f in session_files]
