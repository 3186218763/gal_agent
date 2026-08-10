"""
Character Agent - 角色 Agent
使用 OpenAI Agents SDK
"""
from typing import Dict, Optional
from pydantic import BaseModel, Field

from agents import Agent, Runner

from ..models import CharacterRelationship


class CharacterContext(BaseModel):
    """角色上下文"""
    relationship: Dict[str, int] = Field(default_factory=dict)
    memories: list[str] = Field(default_factory=list)


class CharacterAgent:
    """
    角色 Agent - 扮演 NPC
    使用 OpenAI Agents SDK，每个角色有独立的 agent 和对话历史
    """

    def __init__(
        self,
        character_id: str,
        name: str,
        personality: str
    ):
        self.character_id = character_id
        self.name = name
        self.personality = personality

        # 创建角色专属 agent
        self.agent = Agent[CharacterContext](
            name=name,
            instructions=f"""你扮演角色：{name}

性格设定：{personality}

你需要：
1. 始终保持角色一致性 - 按照性格设定说话和行动
2. 记住之前发生的重要事件（会在上下文中提供）
3. 根据与玩家的关系状态调整态度
4. 生成自然、符合情境的对话

重要规则：
- 不要透露你是 AI
- 不要说"作为 {name}"这样的元话语
- 直接以第一人称说话，不要加"我说："这样的前缀
- 对话要简洁（1-3 句话），不要长篇大论
- 根据信任度调整语气：高信任=友好坦诚，低信任=警惕冷淡""",
        )

        # 对话历史（用于保持会话连续性）
        self.conversation_history = []

    async def speak(
        self,
        context: str,
        relationship: CharacterRelationship,
        additional_context: Optional[str] = None,
        character_context: Optional[CharacterContext] = None
    ) -> str:
        """
        生成角色对话

        Args:
            context: 当前情境描述
            relationship: 与玩家的关系状态
            additional_context: 额外上下文（可选）
            character_context: 角色上下文（可选）

        Returns:
            角色的对话
        """
        if character_context is None:
            character_context = CharacterContext(
                relationship={
                    "trust": relationship.trust,
                    "romance": relationship.romance
                }
            )

        trust_level = relationship.trust
        romance_level = relationship.romance

        # 态度描述
        trust_desc = self._get_trust_description(trust_level)
        romance_desc = self._get_romance_description(romance_level)

        # 构建提示
        prompt = f"""当前情境：
{context}

你对玩家的态度：
- 信任度：{trust_level}/100 {trust_desc}
- 好感度：{romance_level}/100 {romance_desc}
"""

        if additional_context:
            prompt += f"\n额外信息：\n{additional_context}\n"

        if character_context.memories:
            prompt += f"\n你记得的重要事件：\n"
            for memory in character_context.memories[-5:]:  # 只提供最近 5 条记忆
                prompt += f"- {memory}\n"

        prompt += "\n根据当前情境和你对玩家的态度，说一段符合你性格的话。直接输出对话内容，不要任何前缀或标记。"

        # 构建输入历史
        input_list = self.conversation_history.copy()
        input_list.append({"role": "user", "content": prompt})

        # 运行 agent
        result = await Runner.run(
            self.agent,
            input=input_list,
            context=character_context
        )

        dialogue = result.final_output

        # 清理可能的多余标记
        dialogue = self._clean_dialogue(dialogue)

        # 更新对话历史
        self.conversation_history = result.to_input_list()

        return dialogue

    def _get_trust_description(self, trust_level: int) -> str:
        """获取信任度描述"""
        if trust_level >= 80:
            return "(非常信任)"
        elif trust_level >= 60:
            return "(比较信任)"
        elif trust_level >= 40:
            return "(中立)"
        elif trust_level >= 20:
            return "(有戒心)"
        else:
            return "(非常警惕)"

    def _get_romance_description(self, romance_level: int) -> str:
        """获取好感度描述"""
        if romance_level >= 80:
            return "(深厚感情)"
        elif romance_level >= 60:
            return "(有好感)"
        elif romance_level >= 40:
            return "(友好)"
        elif romance_level >= 20:
            return "(普通)"
        else:
            return "(冷淡)"

    def _clean_dialogue(self, dialogue: str) -> str:
        """清理对话文本中的多余标记"""
        # 移除可能的引号包裹
        dialogue = dialogue.strip('"\'')

        # 移除常见的多余前缀
        prefixes = [
            f"{self.name}:",
            f"{self.name}说:",
            f"{self.name}：",
            "我说:",
            "我：",
            "[",
            "「"
        ]

        for prefix in prefixes:
            if dialogue.startswith(prefix):
                dialogue = dialogue[len(prefix):].strip()

        # 移除可能的后缀标记
        dialogue = dialogue.rstrip("」]")

        return dialogue.strip()

    def add_memory(self, memory: str, character_context: CharacterContext):
        """
        添加记忆到角色上下文

        Args:
            memory: 要记住的事件描述
            character_context: 角色上下文
        """
        character_context.memories.append(memory)


class CharacterFactory:
    """角色工厂 - 根据章节元数据创建角色 agents"""

    def __init__(self):
        self.characters: Dict[str, CharacterAgent] = {}
        self.contexts: Dict[str, CharacterContext] = {}

    def create_character(
        self,
        character_id: str,
        name: str,
        personality: str
    ) -> CharacterAgent:
        """创建新角色 agent"""
        if character_id in self.characters:
            return self.characters[character_id]

        agent = CharacterAgent(
            character_id=character_id,
            name=name,
            personality=personality
        )

        self.characters[character_id] = agent
        self.contexts[character_id] = CharacterContext()
        return agent

    def get_character(self, character_id: str) -> Optional[CharacterAgent]:
        """获取已创建的角色 agent"""
        return self.characters.get(character_id)

    def get_context(self, character_id: str) -> Optional[CharacterContext]:
        """获取角色上下文"""
        return self.contexts.get(character_id)

    def clear_all(self):
        """清除所有角色"""
        self.characters.clear()
        self.contexts.clear()
