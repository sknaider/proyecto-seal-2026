import { useState, useEffect, useCallback, useRef } from 'react';
import { api } from '../lib/api';
import { sealWS } from '../lib/ws';
import { normalizeMessage } from '../lib/mappers';
import type { Message, Channel } from '../types';

export function useChat() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [activeChannel, setActiveChannel] = useState('general');
  const [messages, setMessages] = useState<Message[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(false);
  const [typingAgents, setTypingAgents] = useState<string[]>([]);
  const seenIds = useRef(new Set<string>());
  const typingTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  // Load channels
  useEffect(() => {
    api.channels()
      .then(res => setChannels(res.channels || []))
      .catch(() => {});
  }, []);

  // Load messages when channel changes
  useEffect(() => {
    setLoading(true);
    seenIds.current.clear();
    api.messages(activeChannel, 50)
      .then(res => {
        const raw = res.messages || [];
        const msgs = raw.map(normalizeMessage);
        msgs.forEach((m: Message) => seenIds.current.add(m.id));
        setMessages(msgs);
      })
      .catch(() => setMessages([]))
      .finally(() => setLoading(false));
  }, [activeChannel]);

  // WebSocket messages
  useEffect(() => {
    const unsubscribe = sealWS.subscribe((data) => {
      if (data.type === 'connection') {
        setConnected(data.status === 'connected');
        return;
      }

      // Skip heartbeat and non-message events
      if (data.heartbeat || data.error || data.status) return;

      // Handle typing events
      if (data.type === 'typing') {
        const agent = data.from;
        if (!agent) return;
        // Add to typing list
        setTypingAgents(prev => prev.includes(agent) ? prev : [...prev, agent]);
        // Clear previous timer for this agent
        const prevTimer = typingTimers.current.get(agent);
        if (prevTimer) clearTimeout(prevTimer);
        // Auto-remove after 4s
        const timer = setTimeout(() => {
          setTypingAgents(prev => prev.filter(a => a !== agent));
          typingTimers.current.delete(agent);
        }, 4000);
        typingTimers.current.set(agent, timer);
        return;
      }

      // When agent sends a message, clear their typing state
      if (data.from) {
        setTypingAgents(prev => prev.filter(a => a !== data.from));
        const prevTimer = typingTimers.current.get(data.from);
        if (prevTimer) {
          clearTimeout(prevTimer);
          typingTimers.current.delete(data.from);
        }
      }

      // Normalize and add
      if (data.id || data.message || data.content) {
        const msg = normalizeMessage(data);
        if (!seenIds.current.has(msg.id)) {
          seenIds.current.add(msg.id);
          const msgChannel = msg.channel || 'general';
          if (msgChannel === activeChannel || msgChannel === 'web_chat' || msgChannel === 'general') {
            setMessages(prev => [...prev, msg]);
          }
        }
      }
    });
    return () => { unsubscribe(); };
  }, [activeChannel]);

  const sendMessage = useCallback(async (text: string, type = 'text') => {
    if (!text.trim()) return;
    try {
      await api.send(text, activeChannel, type);
    } catch (e) {
      console.error('Send failed:', e);
    }
  }, [activeChannel]);

  const switchChannel = useCallback((channel: string) => {
    setActiveChannel(channel);
  }, []);

  const createDM = useCallback(async (target: string) => {
    const res = await api.createDM(target);
    if (res.channel) {
      setChannels(prev => {
        if (prev.find(c => c.name === res.channel.name)) return prev;
        return [...prev, res.channel];
      });
      setActiveChannel(res.channel.name);
    }
  }, []);

  return {
    channels, activeChannel, messages, connected, loading, typingAgents,
    sendMessage, switchChannel, createDM,
  };
}
