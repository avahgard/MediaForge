"""Small shell coordinator; each feature keeps its own use cases and controls."""
from PySide6.QtCore import QObject,Property,Signal,Slot,QUrl
from pathlib import Path


class Navigation(QObject):
    changed=Signal()

    def __init__(self,media,dub,models,parent=None):
        super().__init__(parent)
        self.media,self.dub,self.models=media,dub,models;self._page='Media'
        self._original_key=None;self._inspected_key=None;self._syncing=False;self._replacement=None
        dub.source_chooser=self.openSource
        dub.before_preview=media.playback.pauseForExternal if media.playback is not None else None
        media.contentChanged.connect(self._sync_source)
        media.workspaceChanged.connect(self._sync_source)
        media.canOpenChanged.connect(self._sync_source)
        media.sourceSelectionCancelled.connect(self._cancel_replacement)
        dub.activityChanged.connect(self._activity)
        if hasattr(models, 'activityChanged'):
            models.activityChanged.connect(self._activity)
        if media.playback is not None:media.playback.changed.connect(self._source_audio)

    def _source_audio(self):
        if self.media.playback.playing:self.dub.player.stop()

    def _activity(self):
        self.media.canOpenChanged.emit()
        self.changed.emit()

    def _cancel_replacement(self):
        self._replacement = None

    def _sync_source(self):
        if self._syncing:return
        self._syncing=True
        try:
            reference=self.media.original_source()
            key=reference.physical_key if reference else None
            if self._replacement and self.media.canImport:
                old_id,path=self._replacement
                if reference and self.media.active_matches_source(path):
                    self._replacement=None
                    if self.media.activeMediaId!=old_id:self.media._remove_media(old_id,shared=True)
                elif self.media.errorMessage:
                    self._replacement=None
            if key!=self._original_key and not self.dub.busy:
                if self.dub.select_original(reference):
                    self._original_key=key;self._inspected_key=None
            if (self._page=='Dub' and key is not None and key!=self._inspected_key
                    and self.media.canImport and reference.kind in ('video','audio')):
                # A failed qualification belongs to this source too. Never
                # automatically retry it on every UI refresh or tab switch.
                if self.dub.inspect_original():self._inspected_key=key
        finally:
            self._syncing=False
            self.changed.emit()

    @Property(bool,notify=changed)
    def canImport(self):
        return self._page in ('Media','Dub') and self.media.canImport and not self.dub.busy and not self.models.busy

    @Slot(QUrl,result=bool)
    def openSource(self,url):
        if not self.canImport or not url.isLocalFile():return False
        self._cancel_replacement()
        self.dub.player.stop();self.media.playback.pauseForExternal()
        return self.media.open_shared_source(url)

    @Slot(str,QUrl,result=bool)
    def replaceSource(self,item_id,url):
        if not self.canImport or not url.isLocalFile():return False
        if not any(row['id']==item_id for row in self.media.mediaItems):return False
        accepted=self.openSource(url)
        if accepted:
            self._replacement=(item_id,url.toLocalFile())
        self._sync_source()
        return accepted

    @Slot(result=bool)
    def clearSource(self):
        if not self.canImport:return False
        self._cancel_replacement()
        self.dub.player.stop();self.media.playback.pauseForExternal()
        return self.media.clear_shared_source()

    @Slot(str,result=bool)
    def selectSource(self,item_id):
        if not self.canImport:return False
        self._cancel_replacement()
        self.dub.player.stop();self.media.playback.pauseForExternal()
        return self.media._select_media(item_id,shared=True)

    @Slot(str,result=bool)
    def removeSource(self,item_id):
        if not self.canImport:return False
        self._cancel_replacement()
        self.dub.player.stop();self.media.playback.pauseForExternal()
        return self.media._remove_media(item_id,shared=True)

    @Slot('QVariantList',result=bool)
    def canDropSource(self,urls):
        if not self.canImport or len(urls)!=1:return False
        url=QUrl(urls[0])
        return url.isValid() and url.isLocalFile() and Path(url.toLocalFile()).is_absolute() and Path(url.toLocalFile()).is_file()

    @Slot('QVariantList',result=bool)
    def dropSource(self,urls):
        return self.canDropSource(urls) and self.openSource(QUrl(urls[0]))

    @Property(str,notify=changed)
    def page(self):return self._page

    @Slot(str,result=bool)
    def navigate(self,page):
        if page not in ('Media','Dub','Settings'):return False
        if page==self._page:return True
        # Navigation does not mutate the selected source or frozen job options.
        # Source/configuration commands retain their own ProcessingGate checks.
        if self._page=='Media' and self.media.playback is not None:
            self.media.playback.pauseForExternal()
        if self._page=='Dub':self.dub.player.stop()
        self._page=page;self.media.setExternalPage(page!='Media');self._sync_source();self.changed.emit();return True
