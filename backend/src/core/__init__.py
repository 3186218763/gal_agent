"""
Core 包初始化
"""
from .script_parser import ScriptParser
from .state_manager import StateManager
from .option_trigger import should_trigger_option, calculate_tension
from .option_generator import validate_options
from .ending_evaluator import EndingEvaluator
from .world_store import WorldStore

# GameLoop remains in game_loop.py for legacy; not imported here so core
# can load without OpenAI Agents SDK (Task 10 uses GameKernel instead).

__all__ = [
    "ScriptParser",
    "StateManager",
    "should_trigger_option",
    "calculate_tension",
    "validate_options",
    "EndingEvaluator",
    "WorldStore",
]
