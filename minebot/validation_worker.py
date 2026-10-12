"""Trusted adapter worker. Run in a credential-free subprocess, never import source code."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

MAX_REPORT_BYTES = 2 * 1024**2
REDSTONE = {
    'redstone_wire', 'redstone_block', 'redstone_torch', 'redstone_wall_torch',
    'repeater', 'comparator', 'observer', 'piston', 'sticky_piston', 'piston_head',
    'moving_piston', 'dispenser', 'dropper', 'hopper', 'lever', 'tripwire_hook',
    'tripwire', 'target', 'daylight_detector', 'detector_rail', 'powered_rail',
    'activator_rail', 'sculk_sensor', 'calibrated_sculk_sensor', 'trapped_chest',
}


class LimitedOutput:
    """Keep the existing writer's exclusive-create semantics with a byte ceiling."""
    def __init__(self, path, limit):
        self.path, self.limit = path, limit

    def open(self, mode):
        return LimitedFile(self.path.open(mode), self.limit)


class LimitedFile:
    def __init__(self, file, limit):
        self.file, self.limit, self.written = file, limit, 0

    def write(self, content):
        if self.written + len(content) > self.limit:
            raise ValueError('Converted file exceeds the 64 MiB limit')
        count = self.file.write(content)
        self.written += count
        return count

    def __getattr__(self, name):
        return getattr(self.file, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.file.close()


def redstone_measurement(build, pipeline):
    counts = Counter()
    for record in build['regions']:
        palette = pipeline.decode_tag(record['region_nbt'])['BlockStatePalette']
        for index, count in Counter(record['indices_x_fastest']).items():
            name = str(palette[index]['Name'])
            block = name.removeprefix('minecraft:')
            if name.startswith('minecraft:') and (block in REDSTONE or block.endswith(('_button', '_pressure_plate', 'copper_bulb'))):
                counts[name] += count
    return {'matched_block_count': sum(counts.values()), 'block_counts': dict(counts),
            'origin': 'measured_redstone_v1', 'confirmed': False,
            'counts_policy': 'Sum across retained regions; overlapping regions are not flattened',
            'scope': 'Known vanilla component IDs; no circuit simulation or proof of absence',
            'house_dataset_recommendation': 'exclude_pending_review' if counts else 'human_review_required'}


def validate_file(source, output, expected_sha256):
    # -I removes inherited Python path customizations. Only this trusted project is added.
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from reviewer import comparison as cmp
    report = {'schema_version': 1, 'passed': False, 'source_sha256': expected_sha256,
              'converter_fingerprint': cmp.fingerprint(), 'stage': 'source_identity'}
    try:
        if cmp.digest(source) != expected_sha256:
            raise ValueError('Original source hash changed')
        report['stage'] = 'bounded_load'
        original = cmp.load(source)
        report['inspection'] = cmp.pipeline.summary(original)
        report['redstone'] = redstone_measurement(original, cmp.pipeline)
        report['stage'] = 'independent_source_reader'
        report['independent_source_reader'] = cmp.pipeline.cross_check(source, original)
        report['stage'] = 'conversion'
        converted_path = output / 'converted.litematic'
        cmp.pipeline.write_build(original, LimitedOutput(converted_path, cmp.MAX_FILE_BYTES))
        report['stage'] = 'bounded_converted_load'
        converted = cmp.load(converted_path)
        report['stage'] = 'comparison'
        comparison = cmp.compare(original, converted)
        # Scene arrays and per-voxel markers belong in the reviewer, not queue reports.
        report['comparison'] = {key: value for key, value in comparison.items()
                                if key not in ('original', 'converted', 'markers')}
        report['archive_comparison'] = cmp.pipeline.verify(original, converted)
        report['stage'] = 'independent_converted_reader'
        report['independent_converted_reader'] = cmp.pipeline.cross_check(converted_path, converted)
        report['source_unchanged'] = cmp.digest(source) == expected_sha256
        report['converted'] = {'sha256': cmp.digest(converted_path), 'size': converted_path.stat().st_size}
        report['passed'] = (report['source_unchanged'] and report['comparison']['passed']
                            and report['archive_comparison']['passed']
                            and report['independent_source_reader']['status'] == 'passed'
                            and report['independent_converted_reader']['status'] == 'passed')
        report['stage'] = 'complete'
    except Exception as error:
        # Parser exceptions may contain arbitrary NBT values. Store their class and stage.
        report['error'] = {'kind': type(error).__name__, 'message': 'Adapter validation failed; source retained'}
    return report


def main(argv=None):
    source_name, output_name, expected_sha256 = (sys.argv[1:] if argv is None else argv)
    source, output = Path(source_name), Path(output_name)
    try:
        # Linux gets an additional memory guard; Windows still has byte/voxel/time limits.
        try:
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        except ImportError:
            pass
        report = validate_file(source, output, expected_sha256)
    except Exception as error:
        report = {'schema_version': 1, 'passed': False, 'source_sha256': expected_sha256,
                  'stage': 'worker_setup', 'error': {'kind': type(error).__name__, 'message': 'Validation worker setup failed'}}
    content = json.dumps(report, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(content) > MAX_REPORT_BYTES:
        report = {'schema_version': 1, 'passed': False, 'source_sha256': expected_sha256,
                  'stage': 'report_limit', 'error': {'kind': 'ReportTooLarge', 'message': 'Validation report exceeds 2 MiB'}}
        content = json.dumps(report).encode()
    with (output / 'report.json').open('xb') as target:
        target.write(content)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
