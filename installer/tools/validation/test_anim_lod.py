"""Exercise the title's animation hooks with scripted actor pointers and no game dump."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3] / 'infamous-recomp'


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}', start) + 2] + '\n'


def unit(trainer, stubs):
    constants = ('kPlayerSlots[]', 'kClassCole ', 'kOffHpMax ', 'kOffAnim ')
    declarations = [line for line in trainer.splitlines()
                    if line.startswith('constexpr uint32_t ') and any(k in line for k in constants)]
    helpers = [next(line for line in trainer.splitlines() if line.startswith(prefix))
               for prefix in ('inline bool  heap_ptr(', 'inline float rdf(', 'bool standin_live()')]
    start = stubs.index('extern "C" volatile int g_abx;', stubs.index('v216 ANIMATION LOD'))
    return (PRELUDE + '\n'.join(declarations + helpers) + '\n'
            + function(trainer, 'uint32_t player_object(')
            + function(trainer, 'extern "C" uint32_t infamous_player_anim_object(')
            + function(trainer, 'extern "C" int infamous_trainer_anim_hook(')
            + stubs[start:stubs.index('/* v233: car pop-in', start)] + CHECKS)


PRELUDE = r'''
#include "infamous_region.h"
#include "trainer.h"
#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unordered_map>
struct ppu_context { uint64_t gpr[32], lr; };
static std::unordered_map<uint32_t, uint32_t> mem;
static char guest;
static char* vm_base = &guest;
static uint32_t vm_read32(uint32_t a) { return mem[a]; }
static uint8_t vm_read8(uint32_t a) { return (uint8_t)mem[a]; }
static unsigned frame = 2, poses;
static uint32_t s_si, s_cole;
static bool s_rt_ok;
static void standin_write_bones() { ++poses; }
extern "C" { volatile int g_abx; }
extern "C" unsigned ppu_boot_frames_presented() { return frame; }
'''

CHECKS = r'''
static void note(uint32_t obj, unsigned size) {
    ppu_context ctx{};
    ctx.gpr[3] = obj + 0x684;
    ctx.lr = INF_ANIM_KICK_LR;
    mem[obj + 0x688] = size;
    infamous_anim_lod_note(&ctx);
}
static int skip(uint32_t obj) {
    ppu_context ctx{};
    ctx.gpr[3] = obj;
    return infamous_anim_lod_skip(&ctx);
}
int main(int argc, char** argv) {
    assert(argc == 2);
    constexpr uint32_t actor = 0x10100000, ped = 0x10200000;
    constexpr uint32_t cole_anim = 0x11100000, ped_anim = 0x11200000, other_anim = 0x11300000;
    mem[kPlayerSlots[0]] = mem[kPlayerSlots[1]] = actor;
    mem[kPlayerSlots[2]] = ped;
    mem[actor] = INF_CLASS_COLE;
    mem[actor + kOffHpMax] = mem[actor + kOffHp] = 0x42C80000; // 100
    mem[actor + kOffAnim] = cole_anim;
    mem[ped] = INF_CLASS_PED;
    mem[ped + kOffAnim] = ped_anim;
    if (!std::strcmp(argv[1], "standin")) {
        s_si = ped; s_cole = actor; s_rt_ok = true;
        assert(skip(ped_anim) && poses == 1); // Independent of ANIM_LOD.
        mem[ped + kOffAnim] = cole_anim;
        assert(!skip(cole_anim) && !skip(ped_anim) && poses == 1);
        mem[ped + kOffAnim] = other_anim;
        assert(skip(other_anim) && poses == 2);
        mem[kPlayerSlots[0]] = 0;
        assert(!skip(other_anim) && poses == 2);
        return 0;
    }
    if (!std::strcmp(argv[1], "report")) {
        skip(other_anim);
        frame += 600;
        skip(other_anim);
        return 0;
    }
    if (!std::strcmp(argv[1], "abx")) g_abx = 0x800000;
    const bool all = !std::strcmp(argv[1], "all") || !std::strcmp(argv[1], "dump");
    if (!all) note(cole_anim, 0x30);
    if (!std::strcmp(argv[1], "optout")) {
        assert(skip(cole_anim));
        return 0;
    }
    assert(!skip(cole_anim)); // Same frame and parity as the eligible pedestrian.
    note(ped_anim, 0x30);
    assert(skip(ped_anim));
    assert(skip(other_anim) == all);
    note(other_anim, 0x80);
    assert(skip(other_anim) == all);
    mem[ped_anim + 0x694] = 1;
    assert(!skip(ped_anim));
    mem[ped_anim + 0x694] = 0;
    ++frame;
    assert(!skip(ped_anim));
    ++frame;
    // Same-frame respawn and address reuse, including a formerly eligible NPC.
    mem[actor + kOffAnim] = ped_anim;
    assert(!skip(ped_anim) && skip(cole_anim));
    mem[actor + kOffAnim] = 0;
    assert(!skip(cole_anim));
    mem[actor + kOffAnim] = ped_anim;
    mem[kPlayerSlots[1]] = 0;
    assert(!skip(cole_anim)); // No corroboration.
    mem[kPlayerSlots[1]] = actor;
    mem[ped] = INF_CLASS_COLE;
    assert(!skip(cole_anim)); // Two different Cole actors during reload.
    mem[ped] = INF_CLASS_PED;
    mem[actor] = INF_CLASS_ENEMY;
    assert(!skip(cole_anim)); // An NPC occupies the former actor address.
    mem[actor] = INF_CLASS_COLE;
    assert(skip(cole_anim));
}
'''


class AnimationLodTests(unittest.TestCase):
    def test_player_and_standin_across_reload(self):
        compiler = shutil.which('cl') or shutil.which('clang++') or shutil.which('g++')
        if not compiler:
            self.skipTest('Run in a VS Developer Command Prompt, or put clang++/g++ on PATH')
        code = unit((ROOT / 'trainer.cpp').read_text(encoding='utf-8'),
                    (ROOT / 'stubs.cpp').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            source = work / 'anim.cpp'
            source.write_text(code, encoding='utf-8')
            for region in ('EU', 'US'):
                exe = work / (region + '.exe')
                if Path(compiler).stem.lower() == 'cl':
                    args = [compiler, '/nologo', '/std:c++17', '/EHsc', '/I' + str(ROOT),
                            '/DINFAMOUS_REGION_' + region, str(source), '/Fe:' + str(exe)]
                else:
                    args = [compiler, '-std=c++17', '-I' + str(ROOT), '-DINFAMOUS_REGION_' + region,
                            str(source), '-o', str(exe)]
                built = subprocess.run(args, cwd=work, capture_output=True, text=True)
                self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                for scenario in ('size', 'all', 'dump', 'abx', 'optout', 'standin', 'report'):
                    with self.subTest(region=region, scenario=scenario):
                        env = dict(os.environ, ANIM_LOD='0' if scenario in ('abx', 'standin') else '1',
                                   ANIM_LOD_ALL='1' if scenario in ('all', 'dump') else '0',
                                   ANIMLOD_DUMP='1' if scenario == 'dump' else '0',
                                   ANIM_LOD_MAX='0x70', ANIM_LOD_PLAYER_POS='0' if scenario == 'optout' else '1')
                        ran = subprocess.run([str(exe), scenario], cwd=work, env=env,
                                             capture_output=True, text=True, timeout=10)
                        self.assertEqual(ran.returncode, 0, ran.stderr)
                        if scenario == 'report':
                            self.assertIn('player obj 0x11100000 class 0x0 | guard on', ran.stderr)
                        if scenario == 'dump':
                            self.assertIn('unrecorded anim object 0x11100000 = COLE', ran.stderr)


if __name__ == '__main__':
    unittest.main()
