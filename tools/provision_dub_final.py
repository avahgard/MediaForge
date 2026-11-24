"""Explicit final-delivery test provisioning; never imported by application startup."""
import argparse
import json
from pathlib import Path
import sys

from mediaforge.application.contracts.models import ModelError
from mediaforge.application.services.processing_gate import MediaProcessingGate
from mediaforge.features.media.processing_control import ProcessingControl
from mediaforge.infrastructure.filesystem.profile import ProfilePaths
from mediaforge.infrastructure.models.optional_resources import OptionalResources, load_cpu_profile, MAX_DISK


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--profile', choices=('qwen', 'cpu'), default='qwen')
    parser.add_argument('--install-runtime', action='store_true')
    parser.add_argument('--install-models', action='store_true')
    parser.add_argument('--accept-network-bytes', required=True, type=int)
    parser.add_argument('--disk-ceiling', required=True, type=int)
    args = parser.parse_args()
    service = OptionalResources(ProfilePaths.for_test(args.root), MediaProcessingGate(),
                                profile=load_cpu_profile() if args.profile == 'cpu' else None)
    selected = []
    if args.install_runtime:
        selected.append(service.profile.runtime.model_id)
    if args.install_models:
        selected.extend(s.model_id for s in service.profile.models)
    if not selected or args.accept_network_bytes < service.sizes()['network'] or args.disk_ceiling != MAX_DISK:
        parser.error('Explicit component selection and full profile byte/ceiling acknowledgment required.')
    control = ProcessingControl(lambda: ModelError('cancelled', 'Explicit provisioning cancelled.'))
    current = [None]
    received = [0]
    checkpoint = [0]
    def progress(stage, name, count, total):
        if name != current[0]:
            current[0], received[0], checkpoint[0] = name, 0, 0
            print(json.dumps(dict(stage=stage, name=name, expected=total)), flush=True)
        received[0] += count
        if received[0] - checkpoint[0] >= 64 * 1024**2:
            checkpoint[0] = received[0]
            print(json.dumps(dict(name=name, received=received[0], expected=total)), flush=True)
    print(json.dumps(dict(explicit_test_provisioning=True, selected=selected, **service.sizes())), flush=True)
    try:
        result = service.install(control, progress, selected=selected)
    except KeyboardInterrupt:
        print(json.dumps(dict(ok=False, code='interrupted', ready=False)), flush=True)
        return 2
    except Exception as error:
        print(json.dumps(dict(ok=False, code=getattr(error, 'code', 'download_or_install_failed'),
                              detail=str(error), ready=False)), flush=True)
        return 2
    print(json.dumps(dict(ok=True, inventory=result, ready=False)), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
