"""Dub controls and state presentation. Processing stays in Application."""
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QObject,Property,Signal,Slot,QTimer,QUrl
from PySide6.QtGui import QDesktopServices

from mediaforge.application.contracts.dub import OutputMode
from mediaforge.presentation.bridge.voice_preview import VoicePreviewPlayer


STAGES=dict(inspect='Inspecting source',extract='Extracting source audio',support='Measuring source silence',
    recognize='Recognizing English',activity='Checking speech timing',translate='Translating into Russian',
    load_voice='Loading local Qwen voice',synthesize='Generating Russian speech',fit='Fitting speech to source timing',
    compose='Composing speech',level='Checking loudness and peaks',export='Writing output',
    validate='Independently validating output',publish='Publishing validated result',
    retire_private='Retiring expired private speech')

WARNINGS=dict(estimated_speech_boundaries='Some speech boundaries used bounded ASR/VAD estimates. Source clock and protected silence were preserved.',
    estimated_phrase_timing='Tutorial narration uses approximate phrase timing; long pauses and the source timeline are preserved.',
    phrase_timing_shifted='Narration was redistributed inside continuous speech blocks; some phrase starts differ by more than one second.',
    recognition_uncertain='Some recognition is uncertain. Occasional transcription or translation errors may be audible; all processed phrases were retained.',
    speech_accelerated='Adaptive Tempo used measured acceleration above 1.15× to retain the complete narration.',
    tempo_above_1_6='Some speech exceeded 1.6×. Intelligibility and time-stretch artifacts require audible review.',
    tempo_above_2_0='Some speech exceeded 2.0×. This high-speed audio is not independently listening-qualified.',
    low_level_speech='The measured program loudness remains below target after bounded leveling.')


class DubViewModel(QObject):
    changed=Signal()
    activityChanged=Signal()

    def __init__(self,service,preview,models,parent=None,*,preferences=None,player=None):
        super().__init__(parent)
        self.service,self.preview,self.models=service,preview,models
        self.player=player or VoicePreviewPlayer(self);self.player.setParent(self);self.player.changed.connect(self.changed)
        self._voice='aiden';self._mode='';self._format='mp3';self._track=None;self._folder='';self._filename=''
        self._source_folders = {}
        self._source_filenames: dict[tuple, str] = {}
        self._span_request=None;self._message='';self._state=service.snapshot();self._preview=preview.snapshot()
        self._preferences=preferences
        if preferences is not None:
            try:
                values=preferences.load()
                if values['dub_voice']=='aiden':self._voice='aiden'
                self._format=values['dub_audio_format']
            except Exception as error:self._message=str(error)
        self._source_generation=0
        self._original=None;self.source_chooser=None;self.before_preview=None
        if hasattr(models, 'activityChanged'):
            models.activityChanged.connect(self.changed)
        self._timer=QTimer(self);self._timer.setInterval(150);self._timer.timeout.connect(self.refresh);self._timer.start()

    @Property('QVariantMap',notify=changed)
    def operation(self):return asdict(self._state)

    @Property(str,notify=changed)
    def state(self):return self._state.state

    @Property(bool,notify=changed)
    def busy(self):return self.service.busy or self.preview.busy

    @Property(bool,notify=changed)
    def available(self):return not self.busy and not self.service.gate.busy and not self.service.gate.failure

    @Property(str,notify=changed)
    def message(self):return self._message or self.service.gate.failure or self._state.message

    @Property(str,notify=changed)
    def actionError(self):return self._message

    @Property(str,notify=changed)
    def stage(self):return STAGES.get(self._state.stage,self._state.stage)

    @Property(str,notify=changed)
    def warningsText(self):
        messages=[]
        for code in self._state.warnings:
            text=WARNINGS.get(code,'Processing warning: '+code)
            if code=='phrase_timing_shifted':
                facts=(self.service.last_receipt or {}).get('speech',{}).get('tutorial_v2',{})
                delta=facts.get('maximum_absolute_delta')
                if type(delta) is int and 0<delta<=3900*48000:
                    text+=f' Maximum measured schedule-start displacement from source phrase estimates: {delta/48000:.3f} s.'
            if code=='speech_accelerated':
                facts=(self.service.last_receipt or {}).get('speech',{}).get('tutorial_v2',{})
                value=facts.get('maximum_tempo')
                try:tempo=Fraction(value) if isinstance(value,str) and len(value)<=64 else None
                except (ValueError,ZeroDivisionError):tempo=None
                if tempo is not None and 1<=tempo<=4:text+=f' Maximum applied block tempo: {float(tempo):.4f}×.'
            messages.append(text)
        return '\n'.join(messages)

    @Property('QVariantMap',notify=changed)
    def sourceInfo(self):
        source=self.service.source()
        if source is None:
            ref=self._original
            return (dict(name=Path(ref.path).name,path=ref.path,video=ref.kind=='video',
                         picture='Selected original · '+ref.kind,copyRefusal='') if ref else {})
        v=source.video
        return dict(name=source.identity.path.name,path=str(source.identity.path),video=bool(v),
            picture=f'{v.width} × {v.height} · {v.codec}' if v else 'Audio source',
            copyRefusal=source.copy_refusal.message if source.copy_refusal else '',
            direction='English → Russian')

    @Property('QVariantList',notify=changed)
    def tracks(self):
        source=self.service.source()
        return [dict(id=t.index,name=f'Track {t.index} · {t.codec} · {t.language or "untagged"} · {t.channels} ch',
            eligible=bool(t.clock and not t.refusal),reason=t.refusal.message if t.refusal else '')
            for t in source.audio_tracks] if source else []

    @Property('QVariantList',constant=True)
    def voices(self):return [dict(id=v,name=v.title()+' · Local') for v in self.service.voices]

    @Property(str,notify=changed)
    def voice(self):return self._voice

    @Property(str,notify=changed)
    def voiceLabel(self):
        return 'Aiden · Qwen3-TTS' if self._voice == 'aiden' else self._voice.title()

    @Property(int,notify=changed)
    def voiceIndex(self):return self.service.voices.index(self._voice)

    @Property(str,notify=changed)
    def outputMode(self):return self._mode

    @Property(str,notify=changed)
    def outputFormat(self):return self._format

    @Property(str,notify=changed)
    def folder(self):return self._folder

    @Property(QUrl,notify=changed)
    def folderUrl(self):
        return QUrl.fromLocalFile(self._folder) if self._folder else QUrl()

    @Property(str,notify=changed)
    def filename(self):
        if not self._filename:
            if self._original:
                return Path(self._original.path).stem
            source = self.service.source()
            return source.identity.path.stem if source else ''
        try:
            plan = self._plan()
            return self.service.output_name(self._voice, plan, self._filename).removesuffix(plan.route.suffix)
        except Exception:
            return self._filename

    def _filename_input(self, plan):
        return self._filename or (plan.source.path.stem + plan.route.suffix)

    @Slot(str,result=bool)
    def setFilename(self,value):
        if value:
            try:
                plan = self._plan()
                effective = self.service.output_name(self._voice, plan, value)
                basename = effective.removesuffix(plan.route.suffix)
                if self.service.output_name(self._voice, plan, basename) == effective:
                    value = basename
            except Exception:
                # Keep invalid input available for correction and Start's refusal.
                pass
        if not self._edit('_filename',value):return False
        if self._original and self._original.kind in ('video', 'audio'):
            key = self._original.physical_key
            if value:
                self._source_filenames[key] = value
            else:
                self._source_filenames.pop(key, None)
        return True

    @Property('QVariantList',notify=changed)
    def requirements(self):
        rows={r['id']:r for r in self.models.components}
        groups=(('Whisper Base',('dub-cpu-runtime','whisper-base')),
                ('Argos · EN → RU',('dub-cpu-runtime','argos-en-ru')),
                ('Qwen3-TTS · Aiden',('qwen-runtime-cu128','qwen3-tts-06b-customvoice','qwen3-tts-tokenizer-12hz')))
        result=[]
        for name,keys in groups:
            missing=[rows.get(k) for k in keys if rows.get(k,{}).get('state') not in ('installed_unqualified','verified_unqualified')]
            status=('Installed · verified again before use' if not missing else
                    next((r['status'] for r in missing if r),'Not installed'))
            result.append(dict(name=name,status=status,installed=not missing))
        return result

    @Property(int,notify=changed)
    def selectedTrackIndex(self):
        return next((i for i,t in enumerate(self.tracks) if t['id']==self._track),-1)

    @Property(str,notify=changed)
    def elapsedText(self):
        seconds=int(self.preview.elapsed() if self.preview.busy else self.service.elapsed())
        return f'{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}'

    @Property(bool,notify=changed)
    def canCancel(self):
        return (self.preview.busy and self._preview.state!='cancelling') or self.service.can_cancel()

    @Property(str,notify=changed)
    def blocker(self):
        if self.busy:return ''
        if self.service.gate.failure:return self.service.gate.failure
        if self.service.gate.busy:return 'Another operation owns processing. Wait for it to finish.'
        if self.service.source() is None:return self.message or 'Choose an eligible video or audio source.'
        try:
            plan=self._plan()
            if not self.modelsInstalled:return 'Models unavailable. Open Settings → Dub Models.'
            if not self._folder:return 'Choose a destination folder.'
            self.service.destination(self._voice,self._folder,plan,self._filename_input(plan))
        except Exception as error:return str(error)
        return ''

    @Property(bool,notify=changed)
    def modelsInstalled(self):return self.models.allInstalled

    def _plan(self):return self.service.plan(self._mode or None,self._format,self._track)

    @Property(str,notify=changed)
    def outputSummary(self):
        if self.service.source() is None:return 'Choose a video or audio source.'
        try:
            p=self._plan()
            return (f'{float(p.duration):.3f} s · '+('Original encoded video copied; Russian audio replaces source audio.'
                if p.route.mode==OutputMode.SAME_AS_SOURCE else 'Video will be encoded · MP4 / H.264 + AAC Russian audio'
                if p.route.mode==OutputMode.COMPATIBLE_MP4 else f'{p.route.container.upper()} · 48 kHz mono')+
                (f' Audio outside picture span: {float(p.excluded_audio_before):.3f} s before, {float(p.excluded_audio_after):.3f} s after.'
                 if p.requires_span_acknowledgment else ''))
        except Exception as error:return str(error)

    @Property(str,notify=changed)
    def outputRouteSummary(self):
        try:
            route=self._plan().route
            if route.mode==OutputMode.SAME_AS_SOURCE:return 'Same as Source · '+route.container.upper()
            if route.mode==OutputMode.COMPATIBLE_MP4:return 'Compatible MP4 · H.264 / AAC'
            return 'Audio Only · '+('WAV PCM16' if self._format=='wav' else 'MP3 192 kb/s')
        except Exception:return ''

    @Property(str,notify=changed)
    def spanConsentText(self):
        if self._span_request is None:
            return ''
        _, plan, _ = self._span_request
        portions = []
        for position, duration in (('start', plan.excluded_audio_before), ('end', plan.excluded_audio_after)):
            if duration:
                seconds = f'{float(duration):.9f}'.rstrip('0').rstrip('.')
                if seconds == '0':
                    measured = f'{duration} seconds'
                else:
                    approximate = '' if Fraction(seconds) == duration else 'about '
                    measured = f'{approximate}{seconds} seconds'
                portions.append(f'{measured} at the {position}')
        return f'This output will exclude {" and ".join(portions)} of the source audio.'

    @Property(bool,notify=changed)
    def canStart(self):
        return self.available and not self.blocker

    @Property(bool,notify=changed)
    def canRetrySource(self):
        return (self.available and self._state.state == 'failed' and self._state.stage == 'inspect'
                and self._state.code == 'portable_data_unavailable' and self._original is not None)

    @Slot(result=bool)
    def retrySource(self):
        return self.canRetrySource and self.inspect_original()

    @Property(bool,notify=changed)
    def canRetry(self):return self.canStart and self.service.can_retry()

    @Property('QVariantMap',notify=changed)
    def previewState(self):return asdict(self._preview)

    @Property(bool,notify=changed)
    def canPlay(self):return (not self.busy and self._preview.voice==self._voice
                             and self.preview.fragment() is not None)

    @Property(QObject,constant=True)
    def previewPlayer(self):return self.player

    @Slot()
    def refresh(self):
        self.service.expire_idle_sessions()
        current=self.service.snapshot();preview=self.preview.snapshot()
        elapsed=(self.preview.elapsed() if self.preview.busy else self.service.elapsed())
        signature=(current,preview,self.service.busy,self.preview.busy,self.service.gate.busy,self.modelsInstalled,self.service.can_retry(),int(elapsed) if self.busy else 0,self.service.can_cancel())
        if signature==getattr(self,'_signature',None):return
        if current.generation != self._state.generation:
            self._span_request=None
        self._signature=signature;self._state=current;self._preview=preview
        if current.stage=='' and current.state=='ready' and current.generation!=self._source_generation:
            source=self.service.source();self._source_generation=current.generation
            self._mode='same_as_source' if source and source.video else 'audio';self._track=None
            if source and len(source.audio_tracks)==1:self._track=source.audio_tracks[0].index
        self.changed.emit();self.activityChanged.emit()

    def _invoke(self,action):
        self.player.stop();self._message=''
        try:accepted=action()
        except Exception as error:self._message=str(error);accepted=False
        self.refresh();self.changed.emit();return bool(accepted)

    @Slot(QUrl,result=bool)
    def chooseSource(self,url):
        if not self.available or not url.isLocalFile():return False
        if self.source_chooser is not None:return self.source_chooser(url)
        return self._invoke(lambda:self.service.inspect(Path(url.toLocalFile())))

    def select_original(self, reference):
        if self.busy:return False
        self.player.stop();self._original=reference;self._mode='';self._track=None;self._span_request=None;self._filename=''
        self._message=''
        message=('Images and image sequences must be exported to a physical video before Dub.'
                 if reference and reference.kind not in ('video','audio') else '')
        accepted=self.service.clear_source(message)
        if accepted:
            if reference and reference.kind in ('video', 'audio'):
                self._filename = self._source_filenames.get(reference.physical_key, '')
            self._folder = (self._source_folders.get(reference.physical_key, str(Path(reference.path).parent))
                            if reference and reference.kind in ('video', 'audio') else '')
        self.refresh();self.changed.emit();return accepted

    def inspect_original(self):
        if not self._original or self._original.kind not in ('video','audio') or not self.available:return False
        return self._invoke(lambda:self.service.inspect(Path(self._original.path)))

    @Slot(QUrl,result=bool)
    def chooseFolder(self,url):
        if self.busy or not url.isLocalFile():return False
        path=Path(url.toLocalFile())
        if not path.is_dir():return False
        if not self._edit('_folder',str(path)):return False
        if self._original and self._original.kind in ('video', 'audio'):
            self._source_folders[self._original.physical_key] = str(path)
        return True

    def _edit(self,name,value,*,save=False):
        if self.busy:return False
        if getattr(self,name)==value:return True
        if not self.service.options_changed():return False
        self.player.stop();setattr(self,name,value);self._message=''
        self._span_request=None
        # This is a settings edit, not a newly inspected source. Do not let the
        # next refresh replace the user's output/track choice with defaults.
        self._source_generation=self.service.snapshot().generation
        if save:self._save_preferences()
        self.refresh();self.changed.emit();return True

    @Slot(str)
    def setVoice(self,value):
        if value in self.service.voices:self._edit('_voice',value,save=True)

    @Slot(str)
    def setMode(self,value):
        if self._mode==value:return
        if value in ('same_as_source','video_audio_only','audio','compatible_mp4'):
            self._edit('_mode',value)

    @Slot(str)
    def setFormat(self,value):
        if value in ('mp3','wav'):self._edit('_format',value,save=True)

    def _save_preferences(self):
        if self._preferences is not None:
            try:self._preferences.save(dict(dub_voice=self._voice,dub_audio_format=self._format))
            except Exception as error:self._message='Selection is temporary; preferences could not be saved. '+str(error)

    @Slot(int)
    def selectTrack(self,value):
        if not self.busy:self._edit('_track',value)

    @Slot()
    def cancelSpanConsent(self):
        if self._span_request is not None:
            self._span_request=None
            self.changed.emit()

    @Slot(result=bool)
    def confirmSpanConsent(self):
        pending = self._span_request
        self.cancelSpanConsent()
        if pending is None or not self.canStart:
            return False
        key, plan, retry = pending
        if key != self._start_options() or plan != self._plan() or (retry and not self.canRetry):
            return False
        return self._dispatch(retry, acknowledge=True)

    @Slot(result=bool)
    def generatePreview(self):
        if not self.available or not self.modelsInstalled:return False
        if self.before_preview is not None:self.before_preview()
        return self._invoke(lambda:self.preview.start(self._voice))

    @Slot(result=bool)
    def playPreview(self):
        if not self.canPlay:return False
        if self.before_preview is not None:self.before_preview()
        owner=self.service.gate.acquire()
        if owner is None:return False
        try:return self.player.play(self.preview.fragment())
        finally:self.service.gate.release(owner)

    @Slot(result=bool)
    def start(self):return self._run(False)

    @Slot(result=bool)
    def retry(self):return self._run(True)

    def _run(self,retry):
        if not (self.canRetry if retry else self.canStart):return False
        plan = self._plan()
        if plan.requires_span_acknowledgment:
            self._span_request = (self._start_options(), plan, retry)
            self.changed.emit()
            return False
        return self._dispatch(retry, acknowledge=False)

    def _start_options(self):
        return (self.service.snapshot().generation, self._voice, self._folder,
                self._mode, self._format, self._track, self._filename)

    def _dispatch(self,retry,*,acknowledge):
        return self._invoke(lambda:self.service.start(self._voice,self._folder,mode=self._mode,
            format_=self._format,track=self._track,acknowledge=acknowledge,retry=retry,
            filename=self._filename_input(self._plan())))

    @Slot()
    def cancel(self):self.service.cancel();self.preview.cancel();self.refresh()

    @Slot(result=bool)
    def openFolder(self):
        if self._state.state!='completed' or not self._state.destination:return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._state.destination).parent)))

    def shutdown(self):
        self._timer.stop();self.player.shutdown();self.preview.shutdown();self.service.shutdown()
