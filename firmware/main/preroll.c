#include "preroll.h"
#include <string.h>

void voice_buffer_init(voice_buffer_t *b,voice_block_t *storage)
{
    memset(b,0,sizeof(*b));
    b->history[0]=storage;b->history[1]=storage+VOICE_HISTORY_BLOCKS;
    b->live=storage+2*VOICE_HISTORY_BLOCKS;
}
void voice_buffer_feed(voice_buffer_t *b,const int16_t *mono,bool cache_history)
{
    int64_t sum=0;uint64_t squares=0;
    for (unsigned i=0;i<VOICE_BLOCK_SAMPLES;++i) { int32_t v=mono[i];sum+=v;squares+=(int64_t)v*v; }
    int64_t mean=sum/VOICE_BLOCK_SAMPLES;
    uint32_t energy=(uint32_t)(squares/VOICE_BLOCK_SAMPLES-mean*mean);
    if (cache_history) {
        b->energies[b->head]=energy;
        memcpy(b->history[b->bank][b->head].pcm,mono,sizeof(voice_block_t));
        b->head=(b->head+1)%VOICE_HISTORY_BLOCKS;
        if (b->have<VOICE_HISTORY_BLOCKS) ++b->have;
    } else b->head=b->have=0; // Do not retain previous speaker/diagnostic audio.
    if (b->capturing) {
        memcpy(b->live[b->recorded++].pcm,mono,sizeof(voice_block_t));
        // Ignore the chime for endpoint detection, but retain all of its PCM.
        // Thirty ms of above-floor activity resets a 1.2-second quiet timer.
        if (b->automatic && b->recorded>50) {
            if (energy>b->threshold) {
                if (++b->voiced>=3) { b->speech_seen=true;b->quiet=0; }
            } else { b->voiced=0;++b->quiet; }
            if (b->recorded%2==0 && b->recorded>=200 && b->speech_seen && b->quiet>=120) {
                b->capturing=false;b->reason=1;
            } else if (b->recorded>=500 && !b->speech_seen) {
                b->capturing=false;b->reason=3;
            }
        }
        if (b->recorded==b->limit) { b->capturing=false;b->reason=2; }
    }
}
bool voice_buffer_begin(voice_buffer_t *b)
{
    if (b->session) return false;
    b->prefix=b->have-b->have%2; // Only complete 20-ms wire packets.
    b->frozen_bank=b->bank;
    b->first=(b->head+VOICE_HISTORY_BLOCKS-b->prefix)%VOICE_HISTORY_BLOCKS;
    b->bank^=1;b->head=b->have=b->recorded=0;
    b->session=b->capturing=true;b->failed=false;
    b->automatic=b->speech_seen=false;b->limit=800;b->quiet=b->voiced=b->reason=0;
    return true;
}
bool voice_buffer_begin_auto(voice_buffer_t *b)
{
    // Estimate the room floor from the quietest fifth of recent history.
    // Logarithmic bins avoid sorting or copying PCM in the trigger callback.
    unsigned bins[32]={0},count=b->have;
    for (unsigned i=0;i<count;++i) {
        uint32_t value=b->energies[(b->head+VOICE_HISTORY_BLOCKS-count+i)%VOICE_HISTORY_BLOCKS];
        unsigned bin=0;while (value>1 && bin<30) { value>>=1;++bin; }++bins[bin];
    }
    unsigned bin=0,total=0;
    while (bin<30) { total+=bins[bin];if (total>=count/5+1) break;++bin; }
    uint64_t floor=count?((uint64_t)1<<(bin+1)):1024;
    if (!voice_buffer_begin(b)) return false;
    uint64_t threshold=floor*6;
    b->threshold=threshold<6400?6400:threshold>UINT32_MAX?UINT32_MAX:(uint32_t)threshold;
    b->automatic=true;b->limit=VOICE_LIVE_BLOCKS;
    return true;
}
bool voice_buffer_packet(const voice_buffer_t *b,unsigned sequence,uint8_t *pcm)
{
    if (!b->session || b->failed || sequence>=(b->prefix+VOICE_LIVE_BLOCKS)/2 ||
        sequence*2+2>b->prefix+b->recorded) return false;
    for (unsigned half=0;half<2;++half) {
        unsigned block=sequence*2+half;
        const voice_block_t *source=block<b->prefix?
            &b->history[b->frozen_bank][(b->first+block)%VOICE_HISTORY_BLOCKS]:
            &b->live[block-b->prefix];
        memcpy(pcm+half*sizeof(voice_block_t),source->pcm,sizeof(voice_block_t));
    }
    return true;
}
void voice_buffer_gap(voice_buffer_t *b)
{
    b->head=b->have=0;
    if (b->session) { b->failed=true;b->capturing=false; }
}
void voice_buffer_end(voice_buffer_t *b)
{ b->session=b->capturing=false; }
