// Target-side regression check: exercise the exact production buffer code.
#include "preroll.h"
#include <stdlib.h>
#include <string.h>
#include "esp_heap_caps.h"

bool voice_buffer_selftest(void)
{
    voice_block_t *storage=heap_caps_malloc(VOICE_STORAGE_BLOCKS*sizeof(*storage),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!storage) return false;
    voice_buffer_t b;int16_t mono[VOICE_BLOCK_SAMPLES],packet[VOICE_BLOCK_SAMPLES*2];
    voice_buffer_init(&b,storage);
    bool ok=true;
    for (unsigned block=0;block<251;++block) {
        for (unsigned i=0;i<VOICE_BLOCK_SAMPLES;++i) mono[i]=block;
        voice_buffer_feed(&b,mono,true);
    }
    ok=voice_buffer_begin(&b) && b.prefix==200 && !voice_buffer_begin(&b);
    for (unsigned block=251;block<1051;++block) {
        for (unsigned i=0;i<VOICE_BLOCK_SAMPLES;++i) mono[i]=block;
        // Clearing the new history during our cue must not affect the session.
        voice_buffer_feed(&b,mono,block>350);
    }
    ok=ok && !b.capturing && b.recorded==800;
    for (unsigned n=0;n<500 && ok;++n) {
        ok=voice_buffer_packet(&b,n,(uint8_t *)packet);
        for (unsigned i=0;i<VOICE_BLOCK_SAMPLES*2 && ok;++i)
            ok=packet[i]==(int16_t)(51+n*2+i/VOICE_BLOCK_SAMPLES);
    }
    ok=ok && !voice_buffer_packet(&b,500,(uint8_t *)packet);
    // No rollover may overwrite an untransmitted live session.
    for (unsigned n=0;n<300;++n) voice_buffer_feed(&b,mono,true);
    ok=ok && voice_buffer_packet(&b,0,(uint8_t *)packet) && packet[0]==51;
    voice_buffer_end(&b);
    // Cold start, partial history alignment, no stale data after a gap.
    voice_buffer_gap(&b);
    for (unsigned n=0;n<3;++n) voice_buffer_feed(&b,mono,true);
    ok=ok && voice_buffer_begin(&b) && b.prefix==2;
    ok=ok && voice_buffer_packet(&b,0,(uint8_t *)packet) && !voice_buffer_packet(&b,1,(uint8_t *)packet);
    voice_buffer_gap(&b);
    ok=ok && b.failed && !b.capturing && !voice_buffer_packet(&b,0,(uint8_t *)packet);
    voice_buffer_end(&b);
    ok=ok && voice_buffer_begin(&b) && b.prefix==0;
    voice_buffer_end(&b);
    // Adaptive recording preserves pauses shorter than the quiet interval,
    // ends on an even block, and includes its final quiet samples.
    voice_buffer_gap(&b);
    for (unsigned i=0;i<VOICE_BLOCK_SAMPLES;++i) mono[i]=(i%2)?20:-20;
    for (unsigned n=0;n<200;++n) voice_buffer_feed(&b,mono,true);
    ok=ok && voice_buffer_begin_auto(&b) && b.prefix==200;
    for (unsigned n=0;n<400 && b.capturing;++n) {
        int amplitude=(n<100 || (n>=160 && n<200))?600:20;
        for (unsigned i=0;i<VOICE_BLOCK_SAMPLES;++i) mono[i]=(i%2)?amplitude:-amplitude;
        voice_buffer_feed(&b,mono,true);
        if (n==159) ok=ok && b.capturing; // 600 ms thinking pause.
    }
    ok=ok && !b.capturing && b.reason==1 && b.recorded==320;
    ok=ok && voice_buffer_packet(&b,259,(uint8_t *)packet) && !voice_buffer_packet(&b,260,(uint8_t *)packet);
    voice_buffer_end(&b);
    ok=ok && voice_buffer_begin_auto(&b);
    for (unsigned n=0;n<VOICE_LIVE_BLOCKS && b.capturing;++n) {
        for (unsigned i=0;i<VOICE_BLOCK_SAMPLES;++i) mono[i]=(i%2)?600:-600;
        voice_buffer_feed(&b,mono,true);
    }
    ok=ok && !b.capturing && b.reason==2 && b.recorded==1300;
    voice_buffer_end(&b);voice_buffer_gap(&b);
    ok=ok && voice_buffer_begin_auto(&b);
    memset(mono,0,sizeof(mono));
    for (unsigned n=0;n<500;++n) voice_buffer_feed(&b,mono,true);
    ok=ok && !b.capturing && b.reason==3 && b.recorded==500;
    voice_buffer_end(&b);
    free(storage);return ok;
}
