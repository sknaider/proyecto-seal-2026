import { useState, useRef, useCallback, KeyboardEvent, DragEvent } from 'react';
import { Send, Smile, Paperclip, Mic, MicOff, X, Image as ImageIcon } from 'lucide-react';

const EMOJI_CATEGORIES: Record<string, string[]> = {
  'Frecuentes': ['😀','😂','🤣','😍','🥰','😎','🤔','👍','👎','❤️','🔥','💪','🎉','✅','❌','⚡','🚀','💡','🛡️','⚙️'],
  'Caras': ['😊','😄','😁','😆','😅','🤣','😇','🙂','😉','😌','😋','😜','🤪','😝','🤗','🤫','🤭','😶','😏','😒'],
  'Manos': ['👋','🤚','✋','🖐️','👌','🤌','🤏','✌️','🤞','🫰','🤙','👈','👉','👆','👇','☝️','👍','👎','✊','👊'],
  'Objetos': ['💻','📱','⌨️','🖥️','🖨️','📷','🎤','🎧','📁','📂','📊','📈','🔧','🔨','⚙️','🔑','🔒','🔓','📌','📎'],
  'Naturaleza': ['🌟','⭐','🌙','☀️','🌈','🔥','💧','🌊','🌸','🌺','🍀','🌲','🌴','🏔️','🌋','⚡','❄️','🌪️','🌤️','🌧️'],
};

interface Props {
  onSend: (text: string, type?: string) => void;
  onUpload: (file: File) => void;
  disabled?: boolean;
}

export default function ChatInput({ onSend, onUpload, disabled }: Props) {
  const [text, setText] = useState('');
  const [showEmoji, setShowEmoji] = useState(false);
  const [recording, setRecording] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  const handleSend = useCallback(() => {
    if (!text.trim() || disabled) return;
    onSend(text.trim());
    setText('');
    setShowEmoji(false);
    textRef.current?.focus();
  }, [text, disabled, onSend]);

  const handleKey = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFile = (files: FileList | null) => {
    if (!files?.length) return;
    Array.from(files).forEach(f => onUpload(f));
  };

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    handleFile(e.dataTransfer.files);
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    const items = e.clipboardData.items;
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        const file = item.getAsFile();
        if (file) onUpload(file);
      }
    }
  };

  const toggleRecording = async () => {
    if (recording) {
      mediaRef.current?.stop();
      setRecording(false);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunksRef.current = [];
      mr.ondataavailable = (e) => chunksRef.current.push(e.data);
      mr.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        const file = new File([blob], `audio_${Date.now()}.webm`, { type: 'audio/webm' });
        onUpload(file);
        stream.getTracks().forEach(t => t.stop());
      };
      mr.start();
      mediaRef.current = mr;
      setRecording(true);
    } catch {
      console.error('Mic access denied');
    }
  };

  const insertEmoji = (emoji: string) => {
    setText(prev => prev + emoji);
    textRef.current?.focus();
  };

  return (
    <div className="border-t border-seal-700 bg-seal-800 relative">
      {/* Drag overlay */}
      {dragOver && (
        <div className="absolute inset-0 bg-seal-accent/10 border-2 border-dashed border-seal-accent rounded-lg z-10 flex items-center justify-center">
          <div className="text-seal-accent font-medium flex items-center gap-2">
            <ImageIcon size={20} /> Suelta tu archivo aqui
          </div>
        </div>
      )}

      {/* Emoji picker */}
      {showEmoji && (
        <div className="absolute bottom-full left-0 right-0 mx-4 mb-2 bg-seal-800 border border-seal-600 rounded-xl shadow-2xl max-h-64 overflow-y-auto z-20">
          <div className="p-3">
            {Object.entries(EMOJI_CATEGORIES).map(([cat, emojis]) => (
              <div key={cat} className="mb-3">
                <p className="text-xs text-seal-500 mb-1.5 font-medium">{cat}</p>
                <div className="flex flex-wrap gap-1">
                  {emojis.map(e => (
                    <button
                      key={e}
                      onClick={() => insertEmoji(e)}
                      className="w-8 h-8 flex items-center justify-center hover:bg-seal-700 rounded-md transition-colors text-base"
                    >
                      {e}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Input area */}
      <div
        className="flex items-end gap-2 p-3"
        onDragOver={e => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
      >
        {/* Attachment */}
        <button
          onClick={() => fileRef.current?.click()}
          className="p-2 text-seal-500 hover:text-seal-accent transition-colors rounded-lg hover:bg-seal-700"
        >
          <Paperclip size={18} />
        </button>
        <input
          ref={fileRef}
          type="file"
          className="hidden"
          accept="image/*,audio/*,.pdf,.doc,.docx,.ppt,.pptx,.xls,.xlsx"
          multiple
          onChange={e => handleFile(e.target.files)}
        />

        {/* Emoji */}
        <button
          onClick={() => setShowEmoji(!showEmoji)}
          className={`p-2 transition-colors rounded-lg hover:bg-seal-700 ${showEmoji ? 'text-seal-accent' : 'text-seal-500 hover:text-seal-accent'}`}
        >
          <Smile size={18} />
        </button>

        {/* Text input */}
        <div className="flex-1 relative">
          <textarea
            ref={textRef}
            value={text}
            onChange={e => setText(e.target.value)}
            onKeyDown={handleKey}
            onPaste={handlePaste}
            placeholder="Escribe un mensaje..."
            rows={1}
            className="w-full bg-seal-700 border border-seal-600 rounded-xl px-4 py-2.5 text-sm text-white placeholder-seal-500 focus:outline-none focus:border-seal-accent resize-none transition-colors"
            style={{ maxHeight: '120px', minHeight: '40px' }}
            onInput={e => {
              const el = e.target as HTMLTextAreaElement;
              el.style.height = 'auto';
              el.style.height = Math.min(el.scrollHeight, 120) + 'px';
            }}
          />
        </div>

        {/* Audio */}
        <button
          onClick={toggleRecording}
          className={`p-2 transition-colors rounded-lg ${
            recording
              ? 'text-seal-red bg-seal-red/10 hover:bg-seal-red/20'
              : 'text-seal-500 hover:text-seal-accent hover:bg-seal-700'
          }`}
        >
          {recording ? <MicOff size={18} /> : <Mic size={18} />}
        </button>

        {/* Send */}
        <button
          onClick={handleSend}
          disabled={!text.trim() || disabled}
          className="p-2 bg-seal-accent hover:bg-seal-accent-dim disabled:opacity-30 disabled:hover:bg-seal-accent text-seal-900 rounded-xl transition-colors"
        >
          <Send size={18} />
        </button>
      </div>

      {recording && (
        <div className="px-4 pb-2 flex items-center gap-2">
          <div className="w-2 h-2 bg-seal-red rounded-full animate-pulse" />
          <span className="text-xs text-seal-red">Grabando audio...</span>
          <button onClick={toggleRecording} className="text-xs text-seal-500 hover:text-white">
            <X size={12} /> Cancelar
          </button>
        </div>
      )}
    </div>
  );
}
