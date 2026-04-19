/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        seal: {
          900: '#0a0e1a',
          800: '#111827',
          700: '#1e2a3a',
          600: '#2a3a4e',
          500: '#3b5068',
          400: '#5a7a9a',
          accent: '#00d4ff',
          'accent-dim': '#0098b8',
          green: '#22c55e',
          red: '#ef4444',
          yellow: '#eab308',
          purple: '#a855f7',
        },
      },
    },
  },
  plugins: [],
}
