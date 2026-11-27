"""Bound upload size while preserving a complete goal clip and its audio."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from common import STATE


def compact(data):
    from goal_videos import VideoError
    ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise VideoError('video_compressor_unavailable')
    def info(path):
        p=subprocess.run([ffprobe,'-v','error','-show_format','-show_streams','-of','json',str(path)],capture_output=True,check=True,timeout=20)
        value=json.loads(p.stdout)
        videos=[s for s in value['streams'] if s['codec_type']=='video']
        if len(videos)!=1:
            raise VideoError('invalid_video_streams')
        duration=float(value['format']['duration'])
        if not 0<duration<=180 or abs(float(videos[0].get('duration',0))-duration)>.5:
            raise VideoError('incomplete_video_timeline')
        return duration,any(s['codec_type']=='audio' for s in value['streams']),videos[0]
    def decode(path):
        # Header/duration alone do not prove that the complete video decodes.
        subprocess.run([ffmpeg,'-nostdin','-v','error','-xerror','-err_detect','explode',
                        '-threads','2','-i',str(path),'-map','0:v:0','-map','0:a:0?',
                        '-f','null','-'],capture_output=True,check=True,timeout=120)
    try:
        with tempfile.TemporaryDirectory(prefix='goal-upload-',dir=STATE) as temp:
            raw,out=Path(temp)/'raw.mp4',Path(temp)/'ready.mp4'
            raw.write_bytes(data)
            duration,audio,stream=info(raw)
            if not 0<duration<=180:
                raise VideoError('unsupported_goal_duration')
            decode(raw)
            if len(data)<=6*1024*1024 and stream.get('codec_name')=='h264' and stream.get('pix_fmt')=='yuv420p':
                subprocess.run([ffmpeg,'-nostdin','-v','error','-y','-i',str(raw),'-map','0:v:0','-map','0:a:0?',
                    '-c','copy','-movflags','+faststart',str(out)],capture_output=True,check=True,timeout=60)
            else:
                subprocess.run([ffmpeg,'-nostdin','-v','error','-y','-i',str(raw),'-map','0:v:0','-map','0:a:0?',
                '-vf','scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2,fps=25',
                '-c:v','libx264','-pix_fmt','yuv420p','-profile:v','main','-level:v','3.1','-g','50','-threads','2','-preset','veryfast','-b:v','900k','-maxrate','1100k','-bufsize','2200k',
                '-c:a','aac','-b:a','96k','-movflags','+faststart',str(out)],capture_output=True,check=True,timeout=180)
            final,sound,_=info(out)
            decode(out)
            if abs(final-duration)>.5 or sound!=audio or out.stat().st_size>45*1024*1024:
                raise VideoError('video_compression_check_failed')
            return out.read_bytes()
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        raise VideoError('video_compression_failed') from None
