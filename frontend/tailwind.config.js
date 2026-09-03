/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        janus: {
          50: '#f5f7fa',
          100: '#eaeef4',
          200: '#d0dae7',
          300: '#a7bbd3',
          400: '#7798bb',
          500: '#547ba5',
          600: '#40618a',
          700: '#344e70',
          800: '#2d425d',
          900: '#1e2d40',
          950: '#0b1119',
        },
        surface: {
          50: '#f8fafc',
          100: '#f1f5f9',
          200: '#e2e8f0',
          800: '#141923',
          850: '#0f141d',
          900: '#0a0d14',
        }
      },
      fontSize: {
        '2xs': ['0.75rem', { lineHeight: '1.05rem' }],
        'xs': ['0.875rem', { lineHeight: '1.25rem' }],
        'sm': ['1.0rem', { lineHeight: '1.5rem' }],
        'base': ['1.125rem', { lineHeight: '1.75rem' }],
        'lg': ['1.25rem', { lineHeight: '1.85rem' }],
        'xl': ['1.45rem', { lineHeight: '2.05rem' }],
        '2xl': ['1.75rem', { lineHeight: '2.35rem' }],
        '3xl': ['2.25rem', { lineHeight: '2.75rem' }],
        '4xl': ['2.75rem', { lineHeight: '3.25rem' }],
      },
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'Menlo', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
};
