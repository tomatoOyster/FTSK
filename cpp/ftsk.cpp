// FTSK-4 v1 single-frame reference. All sample processing is independent of Python.
#include <array>
#include <cmath>
#include <complex>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using Byte = std::uint8_t;
using Bytes = std::vector<Byte>;
using Complex = std::complex<double>;
using Symbols = std::array<unsigned, 31>;
constexpr unsigned slot = 32, tones = 31, payload_size = 32;
constexpr std::array<unsigned, 16> header{5,22,11,26,3,17,8,24,13,2,20,10,27,6,19,4};
constexpr std::array<unsigned, 4> coordinates{0,1,3,7};
constexpr unsigned samples = (16 + 31*4)*slot;
using Waves = std::array<std::array<Complex, slot>, tones>;
static_assert(sizeof(float)==4 && std::numeric_limits<float>::is_iec559, "CF32 needs IEEE binary32");

Waves make_waves() {
    Waves waves{};
    const double omega = 2*std::acos(-1.)/slot;
    for (unsigned k=0; k<tones; ++k)
        for (unsigned n=0; n<slot; ++n)
            waves[k][n] = std::polar(1., omega*(static_cast<int>(k)-15)*n);
    return waves;
}

unsigned tone(unsigned symbol, unsigned segment) {
    return (symbol % tones + (symbol / tones)*coordinates[segment]) % tones;
}

std::uint16_t crc16(const Bytes& data) {
    unsigned reg = 0xffff;
    for (Byte b : data) {
        reg ^= unsigned(b) << 8;
        for (unsigned i=0; i<8; ++i)
            reg = ((reg << 1) ^ ((reg & 0x8000u) ? 0x1021u : 0u)) & 0xffffu;
    }
    return static_cast<std::uint16_t>(reg);
}

Symbols pack(Bytes data) {
    const auto crc = crc16(data);
    data.push_back(static_cast<Byte>(crc >> 8));
    data.push_back(static_cast<Byte>(crc));
    Symbols symbols{};
    for (unsigned bit=0; bit<8*data.size(); ++bit)
        symbols[bit/9] |= ((data[bit/8] >> (7-bit%8)) & 1u) << (8-bit%9);
    return symbols;
}

Bytes unpack(const Symbols& symbols) {
    if (symbols.back() & 0x7fu) throw std::runtime_error("nonzero padding");
    Bytes raw(34);
    for (unsigned bit=0; bit<8*raw.size(); ++bit) {
        const unsigned value = (symbols[bit/9] >> (8-bit%9)) & 1u;
        raw[bit/8] |= static_cast<Byte>(value << (7-bit%8));
    }
    const unsigned received = (unsigned(raw[32]) << 8) | raw[33];
    raw.resize(32);
    if (crc16(raw) != received) throw std::runtime_error("CRC mismatch");
    return raw;
}

void append_float(Bytes& out, float value) {
    std::uint32_t bits;
    std::memcpy(&bits, &value, sizeof(bits));
    for (unsigned shift=0; shift<32; shift+=8) out.push_back(static_cast<Byte>(bits >> shift));
}

float read_float(const Byte* p) {
    std::uint32_t bits = 0;
    for (unsigned i=0; i<4; ++i) bits |= std::uint32_t(p[i]) << (8*i);
    float value;
    std::memcpy(&value, &bits, sizeof(value));
    if (!std::isfinite(value)) throw std::runtime_error("non-finite IQ sample");
    return value;
}

Bytes encode(const Bytes& payload, const Waves& waves) {
    Bytes out;
    out.reserve(samples*8);
    auto emit = [&](unsigned k) {
        for (auto s : waves[k]) {
            append_float(out, static_cast<float>(s.real()));
            append_float(out, static_cast<float>(s.imag()));
        }
    };
    for (unsigned k : header) emit(k);
    for (unsigned m : pack(payload))
        for (unsigned i=0; i<4; ++i) emit(tone(m,i));
    return out;
}

Bytes decode_iq(const Complex* iq, const Waves& waves) {
    Complex correlation{};
    double energy = 0;
    for (unsigned n=0; n<header.size()*slot; ++n) {
        correlation += iq[n] * std::conj(waves[header[n/slot]][n%slot]);
        energy += std::norm(iq[n]);
    }
    if (energy == 0 || std::norm(correlation) < 0.055*header.size()*slot*energy)
        throw std::runtime_error("aligned header correlation too low");
    Symbols ordinary{}, normalized{};
    for (unsigned s=0; s<ordinary.size(); ++s) {
        std::array<std::array<double,tones>,4> power{};
        std::array<double,4> scale{};
        for (unsigned i=0; i<4; ++i) {
            const auto start = (header.size()+4*s+i)*slot;
            double total = 0;
            for (unsigned k=0; k<tones; ++k) {
                Complex projection{};
                for (unsigned n=0; n<slot; ++n) projection += iq[start+n]*std::conj(waves[k][n]);
                total += power[i][k] = std::norm(projection);
            }
            scale[i] = total > 0 ? 1/total : 0;
        }
        double max_sum=-1, max_scaled=-1;
        for (unsigned m=0; m<512; ++m) {
            double sum=0, scaled=0;
            for (unsigned i=0; i<4; ++i) {
                const auto p = power[i][tone(m,i)];
                sum += p;
                scaled += p*scale[i];
            }
            if (sum>max_sum) {max_sum=sum; ordinary[s]=m;}
            if (scaled>max_scaled) {max_scaled=scaled; normalized[s]=m;}
        }
    }
    try { return unpack(ordinary); }
    catch (const std::runtime_error&) { return unpack(normalized); }
}

Bytes decode(const Bytes& wire,const Waves& waves) {
    std::vector<Complex> iq(samples);
    for(unsigned i=0;i<samples;++i) iq[i]={read_float(&wire[8*i]),read_float(&wire[8*i+4])};
    return decode_iq(iq.data(),waves);
}

#include "blind.hpp"

// A sibling directory is reserved atomically. Publication through a hard link
// cannot clobber a path created between validation and commit. No fallback to
// rename (which can replace the destination on POSIX).
void write_new(const std::filesystem::path& dest, const Bytes& bytes) {
    namespace fs=std::filesystem;
    if (fs::exists(dest)) throw std::runtime_error("output already exists; choose a new path");
    std::random_device rng;
    fs::path work;
    for (unsigned i=0; i<32; ++i) {
        auto candidate=dest.parent_path()/(".ftsk-stage-"+std::to_string(rng())+"-"+std::to_string(rng()));
        if (fs::create_directory(candidate)) {work=candidate;break;}
    }
    if (work.empty()) throw std::runtime_error("cannot reserve output staging directory");
    const auto file=work/"data";
    struct Cleanup {
        const fs::path& file; const fs::path& dir;
        ~Cleanup(){std::error_code ec; fs::remove(file,ec);fs::remove(dir,ec);}
    } cleanup{file,work};
    std::ofstream out;
    out.exceptions(std::ios::badbit|std::ios::failbit);
    out.open(file,std::ios::binary);
    out.write(reinterpret_cast<const char*>(bytes.data()),static_cast<std::streamsize>(bytes.size()));
    out.flush();out.close();
    fs::create_hard_link(file,dest);
}

Bytes read_exact(const char* path, std::size_t size) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw std::runtime_error(std::string("cannot open input: ")+path);
    Bytes bytes(size);
    if (!input.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(size)) ||
        input.peek() != std::ifstream::traits_type::eof())
        throw std::runtime_error("input must contain exactly "+std::to_string(size)+" bytes");
    if (input.bad()) throw std::runtime_error("input read failed");
    return bytes;
}
}

int main(int argc, char** argv) {
    if ((argc==3 || argc==4) && std::string(argv[1])=="blind") {
        try {
            unsigned long chunk=4096;
            if(argc==4){std::size_t end=0; chunk=std::stoul(argv[3],&end);
                if(end!=std::string(argv[3]).size() || chunk<1 || chunk>65536)
                    throw std::runtime_error("read chunk must be 1..65536 samples");}
            blind_file(argv[2],make_waves(),static_cast<unsigned>(chunk));
            return 0;
        } catch(const std::exception& e){std::cerr<<"ftsk: "<<e.what()<<'\n';return 1;}
    }
    if (argc!=4 || (std::string(argv[1])!="encode" && std::string(argv[1])!="decode")) {
        std::cerr << "Usage: ftsk encode PAYLOAD32 FRAME.cf32\n       ftsk decode FRAME.cf32 PAYLOAD32\n       ftsk blind STREAM.cf32 [read_chunk_samples]\n";
        return 2;
    }
    try {
        const bool tx = std::string(argv[1])=="encode";
        auto in = read_exact(argv[2], tx ? payload_size : samples*8);
        if (std::filesystem::exists(argv[3]) && std::filesystem::equivalent(argv[2],argv[3]))
            throw std::runtime_error("input and output refer to the same file");
        const auto waves = make_waves();
        const auto result = tx ? encode(in,waves) : decode(in,waves);
        write_new(argv[3], result);
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "ftsk: " << error.what() << '\n';
        return 1;
    }
}
