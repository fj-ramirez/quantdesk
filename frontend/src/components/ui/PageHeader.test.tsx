import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PageHeader } from './PageHeader';

describe('PageHeader', () => {
  it('renders the title as the page h1', () => {
    render(<PageHeader title="Opportunities" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Opportunities' })).toBeInTheDocument();
  });

  it('omits description/caveat/actions when not given', () => {
    render(<PageHeader title="Opportunities" />);
    expect(screen.queryByText(/./, { selector: '.page-header__description' })).not.toBeInTheDocument();
    expect(screen.queryByText(/./, { selector: '.page-header__caveat' })).not.toBeInTheDocument();
    expect(screen.queryByText(/./, { selector: '.page-header__actions' })).not.toBeInTheDocument();
  });

  it('renders the reading cue, caveat and actions when given', () => {
    render(
      <PageHeader
        title="Opportunities"
        description="Ranked trade-idea opportunities across the universe."
        caveat="These are suggestions computed from the latest captured chain; nothing here is routed."
        actions={<button type="button">Record now</button>}
      />,
    );
    expect(screen.getByText('Ranked trade-idea opportunities across the universe.')).toBeInTheDocument();
    expect(
      screen.getByText('These are suggestions computed from the latest captured chain; nothing here is routed.'),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Record now' })).toBeInTheDocument();
  });
});
