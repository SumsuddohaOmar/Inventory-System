"""Small, dependency-free QR encoder for short local connection addresses.

Fixed QR Model 2 version 3, low error correction, byte mode, mask 0. This narrow
encoder deliberately rejects long text instead of silently producing a broken
code. It is not a general-purpose QR library. Matrix rules follow the QR Model 2
format; design references are listed in docs/DEPENDENCIES.md. Test on Android
before treating a printed code as the only way to connect; the URL stays visible.
"""


def multiply(left,right):
    """Multiply bytes in GF(256) with the QR field polynomial x^8+x^4+x^3+x^2+1."""
    value=0
    while right:
        if right&1:value^=left
        right>>=1;left<<=1
        if left&256:left^=0x11D
    return value


def codewords(text):
    """Encode at most 53 UTF-8 bytes into 55 data and 15 error-correction bytes."""
    payload=text.encode("utf-8")
    if len(payload)>53:raise ValueError("Connection address is too long for this QR. Use the manual address.")
    bits="0100"+format(len(payload),"08b")+"".join(format(value,"08b") for value in payload)
    bits+="0"*min(4,440-len(bits));bits+="0"*((-len(bits))%8)
    data=[int(bits[index:index+8],2) for index in range(0,len(bits),8)]
    pad=0
    while len(data)<55:data.append((0xEC,0x11)[pad%2]);pad+=1
    polynomial=[1];root=1
    for _ in range(15):
        following=[0]*(len(polynomial)+1)
        for index,coefficient in enumerate(polynomial):
            following[index]^=coefficient
            following[index+1]^=multiply(coefficient,root)
        polynomial=following;root=multiply(root,2)
    dividend=data+[0]*15
    for index in range(55):
        factor=dividend[index]
        for offset,coefficient in enumerate(polynomial):dividend[index+offset]^=multiply(factor,coefficient)
    return data+dividend[-15:]


def matrix(text):
    """Lay out function patterns and zig-zag data; all coordinates are (x,y)."""
    size=29;grid=[[False]*size for _ in range(size)];fixed=[[False]*size for _ in range(size)]
    def mark(x,y,dark):
        if 0<=x<size and 0<=y<size:grid[y][x]=bool(dark);fixed[y][x]=True
    for position in range(size):mark(6,position,position%2==0);mark(position,6,position%2==0)
    for center_x,center_y in ((3,3),(25,3),(3,25)):
        for y in range(-4,5):
            for x in range(-4,5):
                radius=max(abs(x),abs(y));mark(center_x+x,center_y+y,radius in (0,1,3))
    for y in range(-2,3):
        for x in range(-2,3):mark(22+x,22+y,max(abs(x),abs(y))!=1)
    # Low-error-correction format value 01, mask 000, BCH(15,5), fixed XOR mask.
    remainder=8<<10
    for bit in range(14,9,-1):
        if remainder&(1<<bit):remainder^=0x537<<(bit-10)
    format_bits=((8<<10)|remainder)^0x5412
    def bit(index):return (format_bits>>index)&1
    for index in range(6):mark(8,index,bit(index))
    mark(8,7,bit(6));mark(8,8,bit(7));mark(7,8,bit(8))
    for index in range(9,15):mark(14-index,8,bit(index))
    for index in range(8):mark(size-1-index,8,bit(index))
    for index in range(8,15):mark(8,size-15+index,bit(index))
    mark(8,size-8,True)
    stream="".join(format(value,"08b") for value in codewords(text));cursor=0;right=size-1
    while right>=1:
        if right==6:right=5
        upward=((right+1)&2)==0
        for step in range(size):
            y=size-1-step if upward else step
            for x in (right,right-1):
                if fixed[y][x]:continue
                dark=cursor<len(stream) and stream[cursor]=="1";cursor+=1
                grid[y][x]=bool(dark)^((x+y)%2==0)
        right-=2
    if cursor!=567:raise RuntimeError("QR layout did not provide the expected data-module count.")
    return grid


def svg(text):
    """Render modules with a four-module quiet zone; no payload is embedded as XML."""
    grid=matrix(text)
    path="".join(f"M{x+4},{y+4}h1v1h-1z" for y,row in enumerate(grid) for x,dark in enumerate(row) if dark)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 37 37" shape-rendering="crispEdges"><rect width="37" height="37" fill="white"/><path d="{path}" fill="black"/></svg>'
