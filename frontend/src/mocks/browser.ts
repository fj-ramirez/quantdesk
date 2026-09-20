// Dev-only MSW worker. Started from `main.tsx` before the app renders.
//
// T78 moved this up out of `modules/gex/mocks/` so it can register every module's handlers.
import { setupWorker } from 'msw/browser';
import { handlers } from './handlers';

export const worker = setupWorker(...handlers);
