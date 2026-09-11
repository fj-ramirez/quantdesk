import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MetricCard, MetricStrip } from './MetricCard';

describe('MetricCard', () => {
  it('renders the label, value and hint', () => {
    render(<MetricCard label="Scored" value="25" hint="opportunities across the universe" />);
    expect(screen.getByText('Scored')).toBeInTheDocument();
    expect(screen.getByText('25')).toBeInTheDocument();
    expect(screen.getByText('opportunities across the universe')).toBeInTheDocument();
  });

  it('never renders a status by colour alone -- the status label text is always present', () => {
    render(<MetricCard label="Active" value="3" status={{ tone: 'positive', label: 'Active' }} />);
    // Two occurrences of "Active" is fine (label + status text); the point is the status text
    // node itself, not just a coloured dot with no name.
    const statusNode = screen.getAllByText('Active').find((n) => n.className.includes('metric-card__status'));
    expect(statusNode).toBeDefined();
  });

  it('applies a tone-specific class so colour is driven by CSS tokens, never inline hex', () => {
    render(<MetricCard label="Risk" value="—" status={{ tone: 'negative', label: 'Below floor' }} />);
    const statusNode = screen.getByText('Below floor');
    expect(statusNode.className).toContain('metric-card__status--negative');
  });

  it('T69: omitting density renders the same DOM as before -- no compact class, label visible', () => {
    render(<MetricCard label="Scored" value="25" />);
    const label = screen.getByText('Scored');
    expect(label.className).not.toContain('sr-only');
    expect(label.closest('.metric-card')?.className).not.toContain('metric-card--compact');
  });

  it('T69: density="compact" adds the compact class and visually hides (not removes) the label', () => {
    render(<MetricCard label="Option-chain capture freshness" value="5/5 chains fresh" density="compact" />);
    expect(screen.getByText('5/5 chains fresh')).toBeInTheDocument();
    const label = screen.getByText('Option-chain capture freshness');
    expect(label.className).toContain('sr-only');
    expect(label.closest('.metric-card')?.className).toContain('metric-card--compact');
  });
});

describe('MetricStrip', () => {
  it('renders one card per metric under one accessible group name', () => {
    render(
      <MetricStrip
        label="Opportunities at a glance"
        metrics={[
          { metricKey: 'scored', label: 'Scored', value: '25' },
          { metricKey: 'active', label: 'Active', value: '3' },
        ]}
      />,
    );
    const group = screen.getByRole('group', { name: 'Opportunities at a glance' });
    expect(group).toHaveTextContent('Scored');
    expect(group).toHaveTextContent('25');
    expect(group).toHaveTextContent('Active');
    expect(group).toHaveTextContent('3');
  });

  it('T69: omitting className renders the same wrapping div as before', () => {
    render(<MetricStrip label="Test strip" metrics={[{ metricKey: 'a', label: 'A', value: '1' }]} />);
    expect(screen.getByRole('group', { name: 'Test strip' }).className).toBe('metric-strip');
  });

  it('T69: an extra className is additive, appended alongside the base class', () => {
    render(
      <MetricStrip
        label="Test strip"
        className="dashboard-metric-strip"
        metrics={[{ metricKey: 'a', label: 'A', value: '1' }]}
      />,
    );
    const group = screen.getByRole('group', { name: 'Test strip' });
    expect(group.className).toContain('metric-strip');
    expect(group.className).toContain('dashboard-metric-strip');
  });
});
