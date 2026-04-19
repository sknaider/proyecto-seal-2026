import { useState, FormEvent } from 'react';
import { Shield, Eye, EyeOff, Loader2 } from 'lucide-react';

interface Props {
  onLogin: (username: string, password: string) => Promise<void>;
  error: string | null;
}

export default function Login({ onLogin, error }: Props) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPwd, setShowPwd] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await onLogin(username, password);
    } catch { /* error handled by parent */ }
    setSubmitting(false);
  };

  return (
    <div className="h-full flex items-center justify-center bg-seal-900 px-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-seal-700 border border-seal-600 mb-4">
            <Shield className="w-8 h-8 text-seal-accent" />
          </div>
          <h1 className="text-2xl font-bold text-white">SEAL Chat</h1>
          <p className="text-seal-400 text-sm mt-1">Team SEAL Command Center</p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="bg-seal-red/10 border border-seal-red/30 rounded-lg px-4 py-2 text-seal-red text-sm">
              {error}
            </div>
          )}

          <div>
            <label className="block text-sm text-seal-400 mb-1">Usuario</label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              className="w-full bg-seal-800 border border-seal-600 rounded-lg px-4 py-2.5 text-white placeholder-seal-500 focus:outline-none focus:border-seal-accent transition-colors"
              placeholder="william"
              autoFocus
              required
            />
          </div>

          <div>
            <label className="block text-sm text-seal-400 mb-1">Clave</label>
            <div className="relative">
              <input
                type={showPwd ? 'text' : 'password'}
                value={password}
                onChange={e => setPassword(e.target.value)}
                className="w-full bg-seal-800 border border-seal-600 rounded-lg px-4 py-2.5 pr-10 text-white placeholder-seal-500 focus:outline-none focus:border-seal-accent transition-colors"
                placeholder="********"
                required
              />
              <button
                type="button"
                onClick={() => setShowPwd(!showPwd)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-seal-500 hover:text-seal-400"
              >
                {showPwd ? <EyeOff size={18} /> : <Eye size={18} />}
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-seal-accent hover:bg-seal-accent-dim disabled:opacity-50 text-seal-900 font-semibold rounded-lg py-2.5 flex items-center justify-center gap-2 transition-colors"
          >
            {submitting ? <Loader2 size={18} className="animate-spin" /> : null}
            {submitting ? 'Conectando...' : 'Entrar'}
          </button>
        </form>

        <p className="text-center text-seal-500 text-xs mt-6">
          Team SEAL &middot; Encrypted &middot; Local Only
        </p>
      </div>
    </div>
  );
}
