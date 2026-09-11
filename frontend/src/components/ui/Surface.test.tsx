import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Surface } from './Surface';

describe('Surface', () => {
  it('renders its children inside a bordered, padded card by default', () => {
    render(<Surface aria-label="panel">content</Surface>);
    const node = screen.getByLabelText('panel');
    expect(node).toHaveClass('ui-surface--raised', 'ui-surface--bordered', 'ui-surface--padded');
    expect(node).toHaveTextContent('content');
  });

  it('omits the border/padding classes when told to, without losing the surface level class', () => {
    render(
      <Surface aria-label="flat" level="app" bordered={false} padded={false}>
        content
      </Surface>,
    );
    const node = screen.getByLabelText('flat');
    expect(node).toHaveClass('ui-surface--app');
    expect(node).not.toHaveClass('ui-surface--bordered');
    expect(node).not.toHaveClass('ui-surface--padded');
  });

  it('renders as a different element when asked', () => {
    render(
      <Surface as="section" aria-label="section-surface">
        content
      </Surface>,
    );
    expect(screen.getByLabelText('section-surface').tagName).toBe('SECTION');
  });
});
