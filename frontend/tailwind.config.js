/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        canvas: 'hsl(var(--canvas))',
        surface: 'hsl(var(--surface))',
        'muted-surface': 'hsl(var(--muted-surface))',
        foreground: 'hsl(var(--foreground))',
        'muted-foreground': 'hsl(var(--muted-foreground))',
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-fg))',
        },
        selected: {
          DEFAULT: 'hsl(var(--selected))',
          foreground: 'hsl(var(--selected-fg))',
        },
        success: 'hsl(var(--success-fg))',
        warning: 'hsl(var(--warning-fg))',
        danger: 'hsl(var(--danger-fg))',
        ring: 'hsl(var(--focus-ring))',
      },
      borderRadius: {
        sm: '6px',
        DEFAULT: '8px',
        md: '8px',
        lg: '12px',
        xl: '16px',
      },
      fontSize: {
        'headline': ['2rem', { lineHeight: '2.5rem', fontWeight: '600' }],
        'title': ['1.5rem', { lineHeight: '2rem', fontWeight: '600' }],
        'section': ['1.125rem', { lineHeight: '1.625rem', fontWeight: '600' }],
        'body': ['1rem', { lineHeight: '1.625rem', fontWeight: '400' }],
        'label': ['0.875rem', { lineHeight: '1.25rem', fontWeight: '500' }],
        'code': ['0.8125rem', { lineHeight: '1.3125rem', fontWeight: '400' }],
        'meta': ['0.75rem', { lineHeight: '1.125rem', fontWeight: '400' }],
      },
      spacing: {
        '4.5': '1.125rem',
        '18': '4.5rem',
      },
      maxWidth: {
        'prose': '70ch',
      },
      animation: {
        'fade-in': 'fadeIn 150ms ease-out',
        'slide-up': 'slideUp 180ms ease-out',
        'slide-down': 'slideDown 180ms ease-out',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        slideDown: {
          '0%': { opacity: '0', transform: 'translateY(-4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
    },
  },
  plugins: [],
};
