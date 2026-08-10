export interface GameMessage {
  type: 'game_start' | 'narration' | 'dialogue' | 'options' | 'state_update' | 'ending' | 'chapter_complete' | 'error';
  [key: string]: any;
}

export interface GameStartMessage extends GameMessage {
  type: 'game_start';
  /** Pack title string (backend still uses field name `chapter`). */
  chapter: string;
  session_id: string;
}

export interface NarrationMessage extends GameMessage {
  type: 'narration';
  content: string;
  mood?: string;
}

export interface DialogueMessage extends GameMessage {
  type: 'dialogue';
  character: string;
  content: string;
  mood?: string;
}

export interface Option {
  id: string;
  text: string;
  preview?: string;
}

export interface OptionsMessage extends GameMessage {
  type: 'options';
  options: Option[];
}

export interface StateUpdateMessage extends GameMessage {
  type: 'state_update';
  changes: {
    flags?: Record<string, any>;
    relationships?: Record<string, { trust: number; romance: number }>;
    phase?: string;
    tension?: number;
    steps?: number;
  };
}

export interface EndingMessage extends GameMessage {
  type: 'ending';
  ending_id: string;
  title: string;
  content: string;
  ending_type: 'victory' | 'branch' | 'game_over' | 'fallback';
}

export interface PlayerChoiceMessage {
  type: 'player_choice';
  option_index: number;
}

export interface SessionInfo {
  session_id: string;
  pack_id: string;
  steps: number;
  tension: number;
  phase: string;
  /** Legacy optional fields (older sessions / UI). */
  current_chapter?: string;
  current_beat_index?: number;
  tension_level?: number;
}
