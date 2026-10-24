"""Deterministic speech-beat gestures for shoulder bones in BBS player rigs.

Audio supplies emphasis; Rhubarb cues gate motion to speech. These gestures
express rhythm, not the meaning of the words. Saved pose rotations are radians;
the cubic model renderer converts those to its internal degree rotations.
"""
from array import array
from copy import deepcopy
import math
import random
import sys
import wave

VERSION=3

# Degrees for authoring; converted to native BBS radians at the pose boundary.
# A rigid player arm needs a large shoulder angle to bring its hand above the
# waist. Small forward rotations mostly disappear in a frontal camera view.
PEAKS={
    'subtle':{'explain':(60.,48.),'beat':(69.,35.),'offer':(36.,70.)},
    'expressive':{'explain':(70.,58.),'beat':(78.,44.),'offer':(42.,80.)},
}
ELBOW_PEAKS={
    'subtle':{'explain':(28.,30.,58.),'beat':(36.,22.,52.),'offer':(16.,48.,64.)},
    'expressive':{'explain':(32.,36.,66.),'beat':(42.,25.,58.),'offer':(20.,55.,65.)},
}


def energy_envelope(path):
    """20 ms RMS blocks from the same mono PCM WAV used for lip sync."""
    if path is None:
        return []
    energy=[]
    with wave.open(str(path),'rb') as audio:
        if audio.getnchannels()!=1 or audio.getsampwidth()!=2:
            raise ValueError('Gestures require the normalized mono 16-bit WAV.')
        step=max(1,round(audio.getframerate()*.02))
        while True:
            chunk=audio.readframes(step)
            if not chunk:break
            samples=array('h',chunk)
            if sys.byteorder!='little':samples.byteswap()
            energy.append(math.sqrt(sum(float(x)*x for x in samples)/len(samples))/32768)
    return energy


def speech_phrases(analysis):
    """Bridge tiny consonant closures, but let longer pauses settle the hands."""
    phrases=[]
    for cue in analysis['mouthCues']:
        if cue['value']=='X':continue
        start,end=float(cue['start']),float(cue['end'])
        if phrases and start-phrases[-1][1]<.48:
            phrases[-1][1]=end
        else:phrases.append([start,end])
    return phrases


def schedule(analysis,energy,style,seed):
    rng=random.Random(str(seed)+f' gestures v{VERSION}')
    expressive=style=='expressive'
    gap=1.9 if expressive else 2.45
    events=[]
    maximum=max(energy,default=1.) or 1.
    if energy and max(energy)<=.001:return []
    last_side='left'
    previous_variant=None
    duration=analysis['metadata']['duration']
    for start,end in speech_phrases(analysis):
        end=min(end,duration)
        if end-start<.85:continue
        cursor=start+.65
        # Reserve space for a readable hold and a smooth phrase-end recovery.
        while cursor+.55<end:
            limit=min(end-.55,cursor+.65)
            candidates=[i for i in range(max(0,math.ceil(cursor/.02)),min(len(energy),int(limit/.02)+1))]
            peak=max(candidates,key=lambda i:energy[i])*.02 if candidates else (cursor+limit)/2
            power=(energy[min(len(energy)-1,round(peak/.02))]/maximum) if energy else .6
            # Silence is stillness even if a recognizer mislabels its cues.
            if energy and power<.035:
                cursor=limit+.3
                continue
            begin=max(start+.32,peak-.4)
            hold=min(end-.42,peak+rng.uniform(.28,.42))
            finish=min(end-.25,hold+.5)
            if events and begin<=events[-1]['end']+.15:
                cursor=events[-1]['end']+.65
                continue
            both=rng.random() < (.24 if expressive else .12)
            side=('right' if last_side=='left' else 'left') if rng.random()<.65 else last_side
            last_side=side
            variant=rng.choice([v for v in PEAKS[style] if v!=previous_variant])
            previous_variant=variant
            events.append({'start':round(begin,4),'peak':round(peak,4),'end':round(finish,4),
                           'hold':round(hold,4),'side':'both' if both else side,
                           'strength':round(.9+.1*power,4),'variant':variant})
            cursor=peak+gap+rng.uniform(-.1,.3)
    return events


def channels(analysis,audio,mapping,rest_pose,style,seed,*,elbows=None):
    if style=='off':return {},[]
    if style not in ('subtle','expressive'):
        raise ValueError('Choose Off, Subtle or Expressive talking gestures.')
    energy=energy_envelope(audio)
    events=schedule(analysis,energy,style,seed)
    duration=float(math.ceil(analysis['metadata']['duration']*20))
    phrases=speech_phrases(analysis)
    if energy:
        maximum=max(energy)
        phrases=[(start,min(end,duration/20)) for start,end in phrases
                 if max(energy[max(0,int(start/.02)):min(len(energy),math.ceil(end/.02))],default=0)>
                 max(.001,maximum*.035)]
    result={}
    for side,bone in mapping.items():
        neutral={'s':[1.,1.,1.],'r':[0.,0.,0.],'t':[0.,0.,0.],**deepcopy(rest_pose.get(bone,{}))}
        frames={0.:deepcopy(neutral),duration:deepcopy(neutral)}
        elbow=(elbows or {}).get(side)
        lower_neutral={'s':[1.,1.,1.],'r':[0.,0.,0.],'t':[0.,0.,0.],**deepcopy(rest_pose.get(elbow,{}))}
        lower_frames={0.:deepcopy(lower_neutral),duration:deepcopy(lower_neutral)}

        def pose(forward,outward):
            value=deepcopy(neutral)
            value['r']=[neutral['r'][0]+math.radians(forward),neutral['r'][1],
                        neutral['r'][2]+math.radians(outward)*(1 if side=='right' else -1)]
            return value

        def put(seconds,value,bend=0.):
            tick=min(duration,max(0.,round(seconds*20,4)))
            frames[tick]=deepcopy(value)
            if elbow:
                lower=deepcopy(lower_neutral)
                lower['r'][0]+=math.radians(bend)
                lower_frames[tick]=lower

        for index,(start,end) in enumerate(phrases):
            end=min(end,duration/20)
            if end-start<.42:continue
            primary=('left' if index%2==0 else 'right')==side
            if elbow:
                ready=pose(30.,23.) if primary else pose(10.,7.)
                ready_bend=(42. if style=='expressive' else 36.) if primary else 12.
            else:
                ready=pose(*( (51.,25.) if style=='expressive' else (46.,21.) )) if primary else pose(16.,9.)
                ready_bend=0.
            ready_at=min(start+.32,end-.12)
            release=max(ready_at,end-.25)
            settle=min(duration/20,end+.3)
            put(max(0.,start-.1),neutral)
            put(ready_at,ready,ready_bend)
            for event in events:
                if not start<=event['peak']<end or event['side'] not in (side,'both'):continue
                if elbow:
                    forward,outward,bend=ELBOW_PEAKS[style][event['variant']]
                else:
                    forward,outward=PEAKS[style][event['variant']]
                    bend=0.
                amount=event['strength']
                target=pose(forward*amount,outward*amount)
                follow=pose(forward*amount*.96,outward*amount*.97)
                put(event['start'],ready,ready_bend)
                put(event['peak'],target,bend*amount)
                put(event['hold'],follow,bend*amount*.96)
                put(event['end'],ready,ready_bend)
            # Speaking posture is held between accents. The hands only drop
            # fully for a real pause or at the end of the recording.
            put(release,ready,ready_bend)
            put(settle,neutral)
        result['pose/'+bone]={'type':'pose_transform','keyframes':[
            {'tick':tick,'duration':0.,'interp':'cubic_inout','color':16763461,
             'shape':'CIRCLE','value':frames[tick]} for tick in sorted(frames)]}
        if elbow:
            result['pose/'+elbow]={'type':'pose_transform','keyframes':[
                {'tick':tick,'duration':0.,'interp':'cubic_inout','color':16763461,
                 'shape':'CIRCLE','value':lower_frames[tick]} for tick in sorted(lower_frames)]}
    return result,events
