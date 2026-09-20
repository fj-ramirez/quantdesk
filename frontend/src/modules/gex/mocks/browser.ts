// Dev-only MSW worker. Started from `main.tsx` before the app renders, so `npm run dev`
// shows the shell with data even though T11 doesn't exist yet.
import { setupWorker } from 'msw/browser';
import { handlers } from './handlers';

export const worker = setupWorker(...handlers);
