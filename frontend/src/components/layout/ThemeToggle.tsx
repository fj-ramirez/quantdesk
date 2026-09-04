import { useTheme } from '../../theme/ThemeContext';

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const nextLabel = theme === 'light' ? 'dark' : 'light';
  return (
    <button type="button" onClick={toggleTheme} aria-label={`Switch to ${nextLabel} theme`} title={`Switch to ${nextLabel} theme`}>
      {theme === 'light' ? '\u{1F319}' : '\u{2600}\u{FE0F}'}
    </button>
  );
}
