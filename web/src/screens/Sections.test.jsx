import { describe, expect, it } from 'vitest';
import { artifactMeta, artifactTypeLabel } from './Sections';

describe('artifact presentation', () => {
  it('reads a raw type code as a human word', () => {
    expect(artifactTypeLabel({ artifactType: 'report' })).toBe('Report');
    expect(artifactTypeLabel({ artifactType: 'spreadsheet' })).toBe('Spreadsheet');
  });

  it('falls back to a neutral word for an unknown or missing type', () => {
    expect(artifactTypeLabel({ artifactType: 'weird' })).toBe('File');
    expect(artifactTypeLabel({})).toBe('File');
  });

  it('names the author, recency and size', () => {
    const meta = artifactMeta(
      { agentId: 'eng', updatedAt: new Date().toISOString(), sizeBytes: 2048, version: 1 },
      'Engineering',
    );
    expect(meta).toContain('Engineering');
    expect(meta).toContain('2 KB');
  });

  it('shows the version only once it is past the first revision', () => {
    const base = { agentId: 'eng', updatedAt: new Date().toISOString(), sizeBytes: 10 };
    expect(artifactMeta({ ...base, version: 1 }, 'Eng')).not.toContain('v1');
    expect(artifactMeta({ ...base, version: 3 }, 'Eng')).toContain('v3');
  });
});
