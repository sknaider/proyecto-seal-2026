export interface User {
  id: number;
  username: string;
  display_name: string;
  role: 'admin' | 'user' | 'agent';
  avatar_url?: string;
}

export interface Channel {
  id: number;
  name: string;
  type: 'general' | 'dm' | 'private';
  members?: string[];
  unread_count?: number;
  last_message?: Message;
}

export interface Message {
  id: string;
  from: string;
  to: string;
  channel: string;
  type: 'text' | 'image' | 'audio' | 'file' | 'system';
  message: string;
  timestamp: string;
  file_url?: string;
  file_name?: string;
  reply_to?: string;
  reactions?: Record<string, string[]>;
  db_id?: number;
}

export interface AuthState {
  token: string | null;
  user: User | null;
  loading: boolean;
}

export interface ChatState {
  channels: Channel[];
  activeChannel: string;
  messages: Message[];
  connected: boolean;
}
