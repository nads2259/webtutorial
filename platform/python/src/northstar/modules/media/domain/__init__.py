"""Pure media domain (rule 10, LAW-02): assets, accessible alternatives and time-selectors.

Infrastructure-free: no SQLAlchemy, FastAPI or provider SDK is imported here. The aggregate
:class:`~northstar.modules.media.domain.model.MediaAsset` owns the publish-accessibility invariant
(video/audio require transcript + captions; images require alt text or a decorative flag) and
exposes addressable :class:`~northstar.modules.media.domain.time_selectors.TimeSelector` cues and
segments for time-based media.
"""
