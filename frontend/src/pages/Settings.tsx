import { API_BASE_URL } from '../api/client';
import { ThemeToggle } from '../components/layout/ThemeToggle';
import { PageHeader } from '../components/ui/PageHeader';
import { Surface } from '../components/ui/Surface';

/**
 * `/settings`. Nothing here can fail: two facts about the running app (the API base URL it is
 * configured to talk to, and the current colour theme) with one control. T67 adds the shared
 * `PageHeader`/`Surface` treatment for consistency with the rest of the app; the content
 * itself -- API base URL, theme toggle -- is unchanged.
 */
export function Settings() {
  return (
    <div className="settings-page">
      <PageHeader title="Settings" description="Confirm the API base URL and choose a colour theme." />

      <Surface as="section" aria-label="API connection" className="settings-section">
        <p>
          API base URL: <code>{API_BASE_URL}</code> (from <code>VITE_API_BASE_URL</code>)
        </p>
      </Surface>

      <Surface as="section" aria-label="Appearance" className="settings-section">
        <p>
          Theme: <ThemeToggle />
        </p>
      </Surface>
    </div>
  );
}
