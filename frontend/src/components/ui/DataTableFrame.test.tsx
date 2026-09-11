import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DataTableFrame } from './DataTableFrame';

describe('DataTableFrame', () => {
  it('renders the title, reading cue and source timing line around its children', () => {
    render(
      <DataTableFrame
        title="Ranked opportunities"
        readingCue="Ranked by grade, then score."
        sourceTiming="Generated from the 2026-09-10 16:20 ET capture."
      >
        <table>
          <tbody>
            <tr>
              <td>row</td>
            </tr>
          </tbody>
        </table>
      </DataTableFrame>,
    );
    expect(screen.getByRole('heading', { level: 2, name: 'Ranked opportunities' })).toBeInTheDocument();
    expect(screen.getByText('Ranked by grade, then score.')).toBeInTheDocument();
    expect(screen.getByText('Generated from the 2026-09-10 16:20 ET capture.')).toBeInTheDocument();
    expect(screen.getByText('row')).toBeInTheDocument();
  });

  it('omits the reading cue, timing line and actions row when not given', () => {
    render(
      <DataTableFrame title="Ranked opportunities">
        <p>body</p>
      </DataTableFrame>,
    );
    expect(screen.queryByText(/./, { selector: '.data-table-frame__cue' })).not.toBeInTheDocument();
    expect(screen.queryByText(/./, { selector: '.data-table-frame__timing' })).not.toBeInTheDocument();
    expect(screen.queryByText(/./, { selector: '.data-table-frame__actions' })).not.toBeInTheDocument();
  });
});
