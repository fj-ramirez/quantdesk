import { API_BASE_URL } from '../api/client';
import { ThemeToggle } from '../components/layout/ThemeToggle';

export function Settings() {
  return (
    <div>
      <h1>Settings</h1>
      <p>
        API base URL: <code>{API_BASE_URL}</code> (from <code>VITE_API_BASE_URL</code>)
      </p>
      <p>
        Theme: <ThemeToggle />
      </p>
    </div>
  );
}
