// MapCraft's integer-only geological material model. No Minecraft data writes.
typedef unsigned int u32;
__device__ int floor_div(int value, int divisor) {
    return value / divisor - (value < 0 && value % divisor != 0);
}
__device__ u32 noise(int x, int y, int z) {
    u32 v=(u32)x*73856093u ^ (u32)y*19349663u ^ (u32)z*83492791u;
    v ^= v >> 13; v *= 1274126177u; return v ^ (v >> 16);
}
extern "C" __global__ void geology_materials(const int* desc, unsigned char* output, u32 rows) {
    u32 at=blockIdx.x*blockDim.x+threadIdx.x;
    if(at>=rows*4096u)return;
    u32 local=at&4095u; const int* d=desc+(at>>12)*9;
    int x=d[0]+(local>>8),z=d[1]+((local>>4)&15),y=d[2]+(local&15);
    if(y<1 || y>56 || d[3]<0){output[at]=0;return;}
    u32 stratum=noise(floor_div(x,8),floor_div(y,5),floor_div(z,8))%4u;
    unsigned char material=stratum<3 ? (unsigned char)d[3+stratum] : 0;
    if(d[8] && y<44)material=0;
    u32 cluster=noise(floor_div(x,3),floor_div(y,3),floor_div(z,3))%10000u;
    if(noise(x,y,z)%4u==0){
        if(cluster<(u32)d[6])material=13;
        else if(cluster<(u32)(d[6]+d[7]) && y<40)material=14;
    }
    output[at]=material;
}
