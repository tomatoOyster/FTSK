// Included inside the native modem's implementation namespace. No external DSP dependency.
class BlindReceiver {
    static constexpr unsigned hsize=16*slot, fftsize=1024;
    struct Hit { double power=0, freq=0; };
    const Waves& waves;
    std::array<Complex,hsize> ref{};
    std::array<Complex,fftsize/2> twiddle{};
    std::array<unsigned,fftsize> reverse{};
    std::vector<Complex> buf;
    std::uint64_t base=0;
    const double tau=2*std::acos(-1.);

    Hit coherent(std::size_t start, double energy) const {
        if (energy<=0) return {};
        std::array<Complex,fftsize> a{};
        for(unsigned i=0;i<hsize;++i) a[reverse[i]]=buf[start+i]*std::conj(ref[i]);
        for(unsigned size=2;size<=fftsize;size*=2){
            const auto half=size/2;
            for(unsigned k=0;k<fftsize;k+=size) for(unsigned j=0;j<half;++j){
                auto t=a[k+j+half]*twiddle[j*(fftsize/size)], u=a[k+j];
                a[k+j]=u+t; a[k+j+half]=u-t;
            }
        }
        Hit hit;
        for(int k=-48;k<=48;++k){
            const auto power=std::norm(a[(k+static_cast<int>(fftsize))%fftsize])/(energy*hsize);
            if(power>hit.power) hit={power,k*125.};
        }
        return hit;
    }

    void attempt(std::size_t start, double freq) const {
        // Removing grid CFO leaves at most 62.5 Hz for an ideal header.
        // Relative phase of the two 2-ms halves is unambiguous over +/-250 Hz.
        Complex halves[2]{};
        Complex phase=1., step=std::polar(1.,-tau*freq/128000.);
        for(unsigned i=0;i<hsize;++i,phase*=step)
            halves[i/(hsize/2)]+=buf[start+i]*std::conj(ref[i])*phase;
        freq+=std::arg(halves[1]*std::conj(halves[0]))*128000./(tau*(hsize/2));
        step=std::polar(1.,-tau*freq/128000.); phase=1.;
        std::array<Complex,samples> frame{};
        for(unsigned i=0;i<samples;++i,phase*=step) frame[i]=buf[start+i]*phase;
        const auto bytes=decode_iq(frame.data(),waves);
        constexpr char hex[]="0123456789abcdef";
        std::string payload;
        for(auto b:bytes){payload+=hex[b>>4]; payload+=hex[b&15];}
        std::cout<<"{\"start\":"<<(base+start)<<",\"cfo_hz\":"<<freq<<",\"payload_hex\":\""<<payload<<"\"}\n"<<std::flush;
    }
public:
    explicit BlindReceiver(const Waves& w):waves(w){
        for(unsigned i=0;i<hsize;++i) ref[i]=w[header[i/slot]][i%slot];
        for(unsigned i=0;i<fftsize/2;++i) twiddle[i]=std::polar(1.,-tau*i/fftsize);
        for(unsigned i=0;i<fftsize;++i){
            unsigned j=i;
            for(unsigned b=1;b<fftsize;b*=2,j>>=1) reverse[i]=(reverse[i]<<1)|(j&1);
        }
    }
    void feed(const Byte* data, std::size_t count){
        for(std::size_t i=0;i<count;++i) buf.emplace_back(read_float(data+8*i),read_float(data+8*i+4));
        if(buf.size()<samples) return;
        // Lag-one products of each slot have constant phase (including the transition
        // to the next slot). Prefix sums give a CFO-invariant gate in 16 terms/start.
        std::vector<Complex> diff(buf.size());
        std::vector<double> energy(buf.size()+1);
        for(std::size_t i=0;i<buf.size();++i){
            energy[i+1]=energy[i]+std::norm(buf[i]);
            if(i) diff[i]=diff[i-1]+buf[i]*std::conj(buf[i-1]);
        }
        std::size_t pos=0;
        while(pos+samples<=buf.size()){
            auto en=energy[pos+hsize]-energy[pos];
            Complex d{};
            for(unsigned j=0;j<header.size();++j){
                const auto p=pos+j*slot;
                const unsigned width=(j+1==header.size()?slot-1:slot);
                d+=(diff[p+width]-diff[p])*std::conj(waves[header[j]][1]);
            }
            if(en<=0 || std::norm(d)<.105*.105*en*en){++pos;continue;}
            Hit hit=coherent(pos,en);
            if(hit.power<.055){++pos;continue;}
            std::size_t best=pos;
            for(std::size_t p=pos+1;p<=pos+16;++p){
                auto other=coherent(p,energy[p+hsize]-energy[p]);
                if(other.power>hit.power){hit=other;best=p;}
            }
            if(best+samples>buf.size()){pos=best;break;}
            try {attempt(best,hit.freq);pos=best+samples;}
            catch(const std::runtime_error&){pos=best+1;}
        }
        base+=pos;
        buf.erase(buf.begin(),buf.begin()+static_cast<std::ptrdiff_t>(pos));
    }
};

void blind_file(const char* path, const Waves& waves, unsigned chunk){
    std::ifstream in(path,std::ios::binary);
    if(!in) throw std::runtime_error(std::string("cannot open input: ")+path);
    Bytes raw(8*chunk);
    BlindReceiver rx(waves);
    while(in.read(reinterpret_cast<char*>(raw.data()), static_cast<std::streamsize>(raw.size())) || in.gcount()){
        auto n=in.gcount();
        if(n%8) throw std::runtime_error("truncated CF32 sample at EOF");
        rx.feed(raw.data(),static_cast<std::size_t>(n)/8);
    }
    if(in.bad()) throw std::runtime_error("IQ read failed");
}
