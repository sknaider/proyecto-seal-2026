import type { Message } from '../types';

/**
 * Normalize backend message format to our frontend Message type.
 * Backend uses: id, sender_name, content, message_type, created_at
 * Frontend uses: id, from, message, type, timestamp
 */
export function normalizeMessage(raw: any): Message {
  return {
    id: String(raw.id || raw.db_id || raw.idempotency_key || `msg_${Date.now()}_${Math.random()}`),
    from: raw.sender_name || raw.from || 'unknown',
    to: raw.to || '',
    channel: raw.channel || 'general',
    type: mapType(raw.message_type || raw.type || 'text'),
    message: raw.content || raw.message || '',
    timestamp: raw.created_at || raw.timestamp || new Date().toISOString(),
    file_url: raw.file_url || extractFileUrl(raw.metadata),
    file_name: raw.file_name,
    reply_to: raw.reply_to ? String(raw.reply_to) : undefined,
    db_id: raw.id || raw.db_id,
  };
}

function mapType(t: string): Message['type'] {
  if (t === 'image' || t === 'audio' || t === 'file' || t === 'system') return t;
  return 'text';
}

function extractFileUrl(metadata: any): string | undefined {
  if (!metadata) return undefined;
  try {
    const m = typeof metadata === 'string' ? JSON.parse(metadata) : metadata;
    return m.file_url;
  } catch {
    return undefined;
  }
}
