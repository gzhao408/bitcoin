// Copyright (c) 2020 The Bitcoin Core developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#include <bench/bench.h>
#include <streams.h>
#include <test/util/setup_common.h>

static void FindByte(benchmark::Bench& bench)
{
    // Setup
    FILE* file = fsbridge::fopen("streams_tmp", "w+b");
    const size_t fileSize = 200;
    uint8_t b = 0;
    for (size_t i = 0; i < fileSize; ++i) {
        fwrite(&b, 1, 1, file);
    }
    b = 1;
    fwrite(&b, 1, 1, file);
    rewind(file);
    CBufferedFile bf(file, fileSize * 2, fileSize, 0, 0);

    bench.minEpochIterations(100).run([&] {
        bf.SetPos(0);
        bf.FindByte(1);
    });

    // Cleanup
    bf.fclose();
    fs::remove("streams_tmp");
}

BENCHMARK(FindByte);