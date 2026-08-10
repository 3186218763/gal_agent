"""
Director Agent - 控制游戏流程、选项生成和结局判断
使用 OpenAI Agents SDK
"""
from typing import Dict, List, Any
import json
from pydantic import BaseModel, Field

from agents import Agent, Runner, RunContextWrapper
from agents.decorators import tool

from ..models import GameState, NarrativeBeat


class GameContext(BaseModel):
    """游戏上下文，在 agents 之间共享"""
    game_state: Dict[str, Any] = Field(default_factory=dict)
    current_beat: Dict[str, Any] | None = None
    chapter_metadata: Dict[str, Any] | None = None


class OptionCandidate(BaseModel):
    """候选选项"""
    text: str = Field(description="选项文字（玩家视角）")
    predicted_consequences: Dict[str, Any] = Field(
        description="预期后果",
        default_factory=dict
    )
    narrative_impact: str = Field(description="对剧情的影响描述", default="")


@tool
async def should_trigger_option_tool(
    context: RunContextWrapper[GameContext],
    turns_since_last_option: int,
    tension_level: int,
    has_script_marker: bool
) -> Dict[str, Any]:
    """
    判断是否应该触发选项

    Args:
        context: 游戏上下文
        turns_since_last_option: 距离上次选项的轮数
        tension_level: 紧张度 (1-10)
        has_script_marker: 剧本是否标记了选择点

    Returns:
        包含 should_trigger 和 score 的字典
    """
    score = 0

    # 剧本明确标记 (+40)
    if has_script_marker:
        score += 40

    # 对话轮次累积
    if turns_since_last_option >= 6:
        score += (turns_since_last_option - 5) * 5

    # 紧张度
    if tension_level >= 7:
        score += 15

    # 冷却期惩罚
    if turns_since_last_option < 3:
        score -= 30

    return {
        "should_trigger": score >= 50,
        "score": score,
        "reason": f"剧本标记={has_script_marker}, 轮次={turns_since_last_option}, 紧张度={tension_level}"
    }


class DirectorAgent:
    """Director Agent - 游戏导演，使用 OpenAI Agents SDK"""

    def __init__(self):
        self.agent = Agent[GameContext](
            name="Director",
            instructions="""你是一个 Galgame 游戏导演。你的职责：

1. **判断选项触发时机**
   - 使用 should_trigger_option_tool 工具判断是否应该给玩家选择
   - 考虑对话轮次、紧张度、剧本标记

2. **生成玩家选项**
   - 生成 2-4 个有意义、有区分度的选项
   - 每个选项要有清晰的后果预期
   - 选项之间要有明显区别（态度、行动、后果）
   - 避免"假选择"（结果相同的选项）
   - 至少包含一个温和/中立选项

3. **选项质量要求**
   - 预测每个选项对角色关系的影响（trust 和 romance 的变化值）
   - 预测每个选项会设置/改变的剧情标记
   - 给出对剧情的影响描述

输出格式必须是 JSON，包含 options 数组。
""",
            tools=[should_trigger_option_tool],
        )

    async def should_trigger_option(
        self,
        game_state: GameState,
        current_beat: NarrativeBeat,
        context: GameContext
    ) -> Dict[str, Any]:
        """判断是否触发选项"""

        # 更新上下文
        context.game_state = game_state.to_dict()
        context.current_beat = {
            "title": current_beat.title,
            "content": current_beat.content,
            "has_option_point": current_beat.has_option_point
        }

        prompt = f"""
请使用 should_trigger_option_tool 判断现在是否应该给玩家选择机会。

当前情况：
- 距离上次选项：{game_state.dialogue_count_since_last_option} 轮
- 紧张度：{game_state.tension_level}/10
- 剧本标记：{"是" if current_beat.has_option_point else "否"}

调用工具并返回结果。
"""

        result = await Runner.run(
            self.agent,
            input=prompt,
            context=context
        )

        # 从工具调用结果中提取信息
        for item in result.new_items:
            if hasattr(item, 'output') and isinstance(item.output, dict):
                return item.output

        # 如果没有工具调用，返回默认值
        return {"should_trigger": False, "score": 0, "reason": "No tool call"}

    async def generate_options(
        self,
        game_state: GameState,
        current_beat: NarrativeBeat,
        context: GameContext
    ) -> List[OptionCandidate]:
        """生成选项"""

        # 更新上下文
        context.game_state = game_state.to_dict()
        context.current_beat = {
            "title": current_beat.title,
            "content": current_beat.content
        }

        relationships_str = json.dumps(
            {k: v.to_dict() for k, v in game_state.relationships.items()},
            ensure_ascii=False,
            indent=2
        )

        prompt = f"""
当前情境：
{current_beat.content}

游戏状态：
- 剧情标记：{json.dumps(game_state.flags, ensure_ascii=False)}
- 角色关系：{relationships_str}
- 紧张度：{game_state.tension_level}/10

请生成 3-4 个玩家可以选择的行动选项。

要求：
1. 每个选项要有清晰的行动描述（玩家视角）
2. 预测后果（关系变化、flags 设置）
3. 与其他选项有明显区分
4. 至少包含一个温和选项

返回 JSON 格式：
{{
  "options": [
    {{
      "text": "选项文字",
      "predicted_consequences": {{
        "flag_changes": {{"某个标记": true}},
        "relationship_deltas": {{
          "角色ID": {{"trust": 变化值, "romance": 变化值}}
        }}
      }},
      "narrative_impact": "对剧情的影响描述"
    }}
  ]
}}
"""

        result = await Runner.run(
            self.agent,
            input=prompt,
            context=context
        )

        # 提取最终输出的 JSON
        output_text = result.final_output

        try:
            # 尝试解析 JSON
            data = json.loads(output_text)
            options_data = data.get("options", [])

            # 转换为 OptionCandidate 对象
            candidates = []
            for opt_data in options_data:
                candidates.append(OptionCandidate(
                    text=opt_data.get("text", ""),
                    predicted_consequences=opt_data.get("predicted_consequences", {}),
                    narrative_impact=opt_data.get("narrative_impact", "")
                ))

            return candidates[:4]  # 最多返回 4 个

        except json.JSONDecodeError:
            # JSON 解析失败，返回空列表
            print(f"Failed to parse JSON from director output: {output_text}")
            return []
