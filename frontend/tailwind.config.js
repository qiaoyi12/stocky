/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Nunito", "system-ui", "sans-serif"],
        display: ['"Baloo 2"', "Nunito", "sans-serif"],
      },
      colors: {
        brand: {
          50: "#f3f1ff",
          100: "#e9e6ff",
          200: "#d6d0ff",
          300: "#b8adff",
          400: "#9a86fd",
          500: "#8b5cf6",
          600: "#6366f1",
          700: "#5145cd",
          800: "#42389d",
          900: "#372f7e",
        },
      },
      borderRadius: {
        xl2: "1.25rem",
      },
      boxShadow: {
        card: "0 8px 24px -12px rgba(80,70,160,0.25)",
      },
      keyframes: {
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-6px)" },
        },
        wiggle: {
          "0%, 100%": { transform: "rotate(-3deg)" },
          "50%": { transform: "rotate(3deg)" },
        },
      },
      animation: {
        float: "float 3s ease-in-out infinite",
        wiggle: "wiggle 1s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};
