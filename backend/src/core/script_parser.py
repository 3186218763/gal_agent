"""
剧本解析器 - 解析 YAML 元数据和 Markdown 剧情文件
"""
import yaml
import re
from pathlib import Path
from typing import List, Dict, Any
from ..models import (
    ChapterMetadata,
    Character,
    EndingCondition,
    NarrativeBeat,
    BeatType,
    EndingType
)


class ScriptParser:
    """剧本解析器"""

    def __init__(self, scripts_dir: str = "scripts"):
        self.scripts_dir = Path(scripts_dir)

    def parse_chapter(self, chapter_id: str) -> tuple[ChapterMetadata, List[NarrativeBeat]]:
        """
        解析章节

        Args:
            chapter_id: 章节 ID (如 "chapter_01")

        Returns:
            (元数据, beats 列表)
        """
        chapter_path = self.scripts_dir / chapter_id

        if not chapter_path.exists():
            raise FileNotFoundError(f"Chapter not found: {chapter_id}")

        # 解析元数据
        metadata = self._parse_metadata(chapter_path / "metadata.yaml")

        # 解析剧情
        beats = self._parse_plot(chapter_path / "plot.md")

        return metadata, beats

    def _parse_metadata(self, metadata_path: Path) -> ChapterMetadata:
        """解析 YAML 元数据"""
        with open(metadata_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)

        # 解析角色
        characters = [
            Character(
                id=char['id'],
                name=char['name'],
                personality=char['personality'],
                initial_trust=char.get('initial_trust', 50),
                initial_romance=char.get('initial_romance', 0)
            )
            for char in data.get('characters', [])
        ]

        # 解析结局条件
        endings = [
            EndingCondition(
                id=ending['id'],
                condition=ending['condition'],
                type=EndingType(ending['type']),
                priority=ending.get('priority', 50),
                title=ending.get('title', ''),
                content=ending.get('content', '')
            )
            for ending in data.get('endings', [])
        ]

        return ChapterMetadata(
            chapter_id=data['chapter_id'],
            title=data['title'],
            characters=characters,
            endings=endings,
            key_decision_points=data.get('key_decision_points', [])
        )

    def _parse_plot(self, plot_path: Path) -> List[NarrativeBeat]:
        """解析 Markdown 剧情文件"""
        with open(plot_path, 'r', encoding='utf-8') as f:
            content = f.read()

        beats = []

        # 按 ## Beat 分割
        beat_pattern = r'^## Beat \d+:?\s*(.+?)$'
        sections = re.split(beat_pattern, content, flags=re.MULTILINE)

        # sections[0] 是文件头部（章节标题等），跳过
        # 之后每两个元素是：beat 标题, beat 内容
        for i in range(1, len(sections), 2):
            if i + 1 >= len(sections):
                break

            title = sections[i].strip()
            body = sections[i + 1].strip()

            beat = self._parse_beat(title, body)
            beats.append(beat)

        return beats

    def _parse_beat(self, title: str, body: str) -> NarrativeBeat:
        """解析单个 beat"""
        mood = None
        has_option_point = False
        flags_to_set = {}
        character_interactions = []
        content_lines = []

        for line in body.split('\n'):
            line = line.strip()

            # 解析 Mood
            if line.startswith('**Mood**:'):
                mood = line.replace('**Mood**:', '').strip()

            # 检测选项标记
            elif '[OPTION POINT' in line:
                has_option_point = True
                # 不添加到 content 中

            # 解析 flag 设置
            elif line.startswith('Set flag:'):
                # 格式: Set flag: `met_alice = true`
                flag_match = re.search(r'`(.+?)\s*=\s*(.+?)`', line)
                if flag_match:
                    key = flag_match.group(1).strip()
                    value = flag_match.group(2).strip()
                    # 简单类型转换
                    if value.lower() == 'true':
                        flags_to_set[key] = True
                    elif value.lower() == 'false':
                        flags_to_set[key] = False
                    elif value.isdigit():
                        flags_to_set[key] = int(value)
                    else:
                        flags_to_set[key] = value

            # 解析角色交互
            elif line.startswith('Character interaction:'):
                interaction = line.replace('Character interaction:', '').strip()
                character_interactions.append(interaction)

            else:
                # 正常内容
                if line:
                    content_lines.append(line)

        # 判断 beat 类型（简单启发式）
        beat_type = BeatType.NARRATION
        content = '\n'.join(content_lines)

        # 如果包含角色名称或对话标记，可能是对话
        # 这里简化处理，后续可以根据实际需求细化
        if any(keyword in content.lower() for keyword in ['说', '告诉', '问', '回答']):
            beat_type = BeatType.DIALOGUE

        return NarrativeBeat(
            title=title,
            content=content,
            type=beat_type,
            mood=mood,
            has_option_point=has_option_point,
            flags_to_set=flags_to_set,
            character_interactions=character_interactions
        )

    def list_chapters(self) -> List[str]:
        """列出所有可用章节"""
        if not self.scripts_dir.exists():
            return []

        chapters = []
        for item in self.scripts_dir.iterdir():
            if item.is_dir() and (item / "metadata.yaml").exists():
                chapters.append(item.name)

        return sorted(chapters)
