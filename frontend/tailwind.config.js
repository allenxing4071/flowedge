/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx}',
    './components/**/*.{js,ts,jsx,tsx}',
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        /* ── FlowEdge 交易驾驶舱色彩系统 ── */
        /* 底色 — 极深蓝黑，减少眼疲劳 */
        surface: {
          0: '#06060b',    /* 最深背景 */
          1: '#0c0c14',    /* 卡片背景 */
          2: '#12121c',    /* 悬浮/选中 */
          3: '#1a1a28',    /* 边框/分隔 */
        },
        /* 多头绿 */
        bull: {
          DEFAULT: '#00e676',
          dim: '#00e67633',
          glow: '#00e67618',
        },
        /* 空头红 */
        bear: {
          DEFAULT: '#ff1744',
          dim: '#ff174433',
          glow: '#ff174418',
        },
        /* 信息蓝 */
        info: {
          DEFAULT: '#448aff',
          dim: '#448aff33',
        },
        /* 警告琥珀 */
        warn: {
          DEFAULT: '#ffab00',
          dim: '#ffab0033',
        },
        /* 异常紫 */
        anomaly: {
          DEFAULT: '#d500f9',
          dim: '#d500f933',
        },
        /* 文字层级 */
        text: {
          primary: '#f0f0f5',
          secondary: '#8888a0',
          tertiary: '#555570',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'SF Mono', 'monospace'],
      },
      fontSize: {
        'xxs': ['0.625rem', { lineHeight: '0.875rem' }],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'glow': 'glow 2s ease-in-out infinite alternate',
        'slide-up': 'slideUp 0.3s ease-out',
        'fade-in': 'fadeIn 0.2s ease-out',
      },
      keyframes: {
        glow: {
          '0%': { boxShadow: '0 0 5px rgba(0,230,118,0.1)' },
          '100%': { boxShadow: '0 0 20px rgba(0,230,118,0.15)' },
        },
        slideUp: {
          '0%': { transform: 'translateY(8px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
}
