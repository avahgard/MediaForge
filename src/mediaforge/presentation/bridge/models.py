"""Qt state presentation for the one shared optional-resource inventory."""
from dataclasses import asdict
import math

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer


LABELS = {
    'dub-cpu-runtime': 'CPU Runtime',
    'whisper-base': 'Whisper Base',
    'argos-en-ru': 'Argos EN → RU',
    'qwen-runtime-cu128': 'Qwen CUDA Runtime',
    'qwen3-tts-06b-customvoice': 'Qwen3-TTS CustomVoice',
    'qwen3-tts-tokenizer-12hz': 'Qwen 12 Hz Tokenizer',
}
DESCRIPTIONS = dict(zip(LABELS, (
    'Runs speech recognition and translation', 'Recognizes English speech', 'Translates English to Russian',
    'Runs Russian voice synthesis on NVIDIA CUDA', 'Provides the Aiden voice', 'Decodes Qwen speech tokens',
), strict=True))


def size_text(value, *, ceiling=False):
    if value < 1024:
        return f'{value:,} B'
    unit, scale, digits = next((name, size, precision) for name, size, precision in
                               (('GiB', 1024**3, 2), ('MiB', 1024**2, 1), ('KiB', 1024, 1))
                               if value >= size)
    number = value / scale
    if ceiling:
        number = math.ceil(number * 10**digits) / 10**digits
    return f'{number:.{digits}f} {unit}'


class ModelsViewModel(QObject):
    changed = Signal()
    activityChanged = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._state = service.snapshot()
        self._rows = []
        self._message = ''
        self._display_plan = None
        self._busy = False
        self._admission = self.admission
        self._timer = QTimer(self); self._timer.setInterval(200)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self._inventory()

    def _inventory(self):
        try:
            self._rows = [dict(row, name=LABELS[row['id']], description=DESCRIPTIONS[row['id']],
                               size=size_text(row['bytes']) if row.get('present', row['state'] != 'missing') else '',
                               status={'missing': 'Not installed', 'verified_unqualified': 'Verified',
                                       'corrupt': 'Repair required', 'incomplete': 'Interrupted',
                                       'unavailable': 'Unavailable'}.get(row['state'],
                                       'Verification pending')) for row in self.service.inventory()]
        except Exception as error:
            self._rows = []; self._message = str(error)

    @Property('QVariantList', notify=changed)
    def components(self):
        return self._rows

    @Property('QVariantMap', notify=changed)
    def operation(self):
        return asdict(self._state)

    @Property(bool, notify=changed)
    def busy(self):
        return self.service.busy

    @Property(bool, notify=changed)
    def available(self):
        return not self.busy and not self.admission

    @Property(str, notify=changed)
    def admission(self):
        gate = self.service.resources.gate
        return gate.failure or ('Another operation is active.' if gate.busy else '')

    @Property(bool, notify=changed)
    def allInstalled(self):
        return len(self._rows)==6 and all(r['state'] in ('installed_unqualified','verified_unqualified') for r in self._rows)

    @Property(str, notify=changed)
    def message(self):
        return self._message or self._state.message

    @Property(str, constant=True)
    def requirementsText(self):
        sizes = self.service.requirements()
        return f"About {size_text(sizes['network'])} download · About {size_text(sizes['peak'])} planning space"

    @Property('QVariantMap', notify=changed)
    def plan(self):
        plan = self._display_plan
        if plan is None:
            return {}
        facts = [
            ('Maximum download', size_text(plan.network_bytes, ceiling=True) if plan.network_bytes else '0 B · Offline'),
            ('Installed after setup', size_text(plan.installed_bytes)),
            ('Temporary storage (peak)', size_text(plan.temporary_bytes, ceiling=True)),
            ('Required free space', size_text(plan.required_bytes, ceiling=True)),
            ('Available on Data drive', size_text(plan.available_bytes)),
            ('Already installed and retained', size_text(plan.retained_bytes)),
        ]
        steps = []
        scope = []
        component_paths = []
        companion_paths = []
        for step in plan.components:
            label = LABELS[step.key]
            if step.action != 'reuse':
                component_paths.append(step.destination)
                component_path = step.destination.relative_to(step.destination.parents[1])
                scope.append(f"{'Replace damaged' if step.action == 'replace' else 'Install'} {component_path}")
            if step.companion_action in ('publish', 'replace'):
                companion_paths.append(step.companion)
                action = 'Replace damaged' if step.companion_action == 'replace' else 'Publish'
                scope.append(f'{action} {step.companion.name} from the authenticated bundled copy')
            if step.reset_intent:
                scope.append(f'Reset the empty interruption marker for {label}')
            scope.extend(f'Replace damaged cached file {path.name}' for path in step.damaged_assets)
            steps.append(dict(name=label, revision=step.revision))
        reuse = ', '.join(LABELS[key] for key in plan.retained_components) or 'No installed component payloads.'
        count = len({digest for step in plan.components for digest in step.cached_assets})
        if count:
            reuse += f' · {count} verified cached download object(s).'
        network = ('Confirm authorizes downloads from ' + ', '.join(plan.publishers) + '.'
                   if plan.publishers else 'No network request is required.')
        component = str(component_paths[0].parents[1] if len(component_paths) > 1 else component_paths[0]) if component_paths else ''
        companion = str(companion_paths[0].parent if len(companion_paths) > 1 else companion_paths[0]) if companion_paths else ''
        return dict(repair=plan.repair, steps=steps, scope='\n'.join(scope), reuse=reuse, network=network,
                    figures=[dict(label=label, value=value) for label, value in facts],
                    component_destination=component, companion_destination=companion,
                    manifest_only=bool(plan.repair and not component and companion),
                    blocked=plan.blocked_reason)

    @Slot()
    def reloadInventory(self):
        if not self.service.busy:
            self._inventory()
            self.changed.emit()

    @Slot()
    def refresh(self):
        state = self.service.snapshot()
        busy, admission = self.service.busy, self.admission
        if (state, busy, admission) != (self._state, self._busy, self._admission):
            activity_changed = (busy, admission) != (self._busy, self._admission)
            if not busy and (self._busy or state.generation != self._state.generation):
                self._inventory()
            self._busy, self._admission = busy, admission
            self._state = state
            self._display_plan = self.service.pending_plan if not busy and state.state == 'consent' else None
            self.changed.emit()
            if activity_changed:
                self.activityChanged.emit()

    @Slot(str, bool, result=bool)
    def prepare(self, key, repair):
        accepted = self.available and self.service.prepare_plan(key, repair)
        if accepted:
            self._message = ''
            self.refresh()
        return accepted

    @Slot(result=bool)
    def confirm(self):
        accepted = self.available and self.service.confirm_plan(self._display_plan)
        if accepted:
            self.refresh()
        return accepted

    @Slot()
    def dismiss(self):
        self.service.dismiss_plan()
        self.refresh()

    @Slot()
    def cancel(self):
        self.service.cancel(); self.refresh()

    @Slot(result=bool)
    def verify(self):
        if not self.available:return False
        try:accepted=self.service.verify()
        except Exception as error:self._message=str(error);self.changed.emit();return False
        if accepted:self._message='';self.refresh()
        return bool(accepted)

    def shutdown(self):
        self._timer.stop(); self.service.shutdown()
