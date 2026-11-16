# -*- coding: utf-8 -*-
"""AI execution limits shared by the executor, validator, and UI.

These are safety rails rather than hard technical limits:
  · max_steps bounds planner loops and model cost;
  · the token budget stops runaway completions;
  · the repeated-action guard stops identical tool calls.
Changing a ceiling here must be reflected in the UI and validation
automatically because all three import these constants.
"""

AI_DEFAULT_MAX_STEPS = 12
AI_MAX_STEPS_CEILING = 100
AI_RECOMMENDED_TIMEOUT_S = 600
AI_MAX_TOTAL_TOKENS = 200_000
AI_REPEAT_ACTION_LIMIT = 3
