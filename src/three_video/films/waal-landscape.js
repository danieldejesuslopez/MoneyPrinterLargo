import {smooth,mix,palette as p} from './waterworks.js';

const headings=['A river beside a river','One narrow bend','The winter of 1995','Find the bottleneck','Moving the dike','Whose land?','Cutting a second route','Where the flood goes','Two ways through','The flood mark','An island in the city','Room for the Waal'];
const notes=['An island created by a flood project','The Waal at Nijmegen','250,000 evacuated across the Gelderland river region','High water at the bend','350 m inland','Land acquisition and compensation','A side channel about 3 km long','About one third through the side channel at high flow','The channels rejoin downstream','27 cm target   /   35 cm achieved','Bridges reconnect the banks','Completed in 2016'];
export function makeWaal({group,box,rod,ribbon,tree,house,vessel}){
    const main=z=>-.95-.91*Math.cos(z*.36),half=z=>.88+.56*(1-Math.exp(-z*z/19));
    const arch=z=>Math.max(0,1-(z/6.9)**2),branch=z=>main(z)+3.56*arch(z);
    box(0x839079,0,-.57,0,42,1,45);
    const land=box(p.land,0,-.08,0,42,.16,45);
    ribbon(p.water,z=>main(z)-half(z),z=>main(z)+half(z),-18,18,.025);
    const side=ribbon(p.water,z=>branch(z)-.61*arch(z),z=>branch(z)+.61*arch(z),-6.9,6.9,.045);
    const dike=group(),old=group();
    function bank(z){return main(z)+half(z)+.22;}
    for(let j=0;j<40;j++){
        const z=-10+j*.5;
        for(const [g,extra,col] of [[old,0,0xa68551],[dike,3.15,0x8c7953]]){
            const X=q=>bank(q)+extra*arch(q);
            rod(col,[X(z),.10,z],[X(z+.5),.10,z+.5],.135,g);
        }
    }
    for(let row=0;row<6;row++)for(let k=0;k<32;k++){
        const z=k*.80-13,x=main(z)-half(z)-.58-row*.74;
        if(Math.abs(z+2.45)<.43)continue;
        house(x,0,z,.66,[0xb17962,0xbfa081,0x9b6a5b,0x958c7f][(k+row)%4]);
    }
    for(let k=0;k<8;k++){
        const x=5.1+(k%3)*1.13,z=-9+k*2.3;
        box(k%2?0xbdb48f:0xa3a474,x,.014,z,1.65,.02,1.75);
        house(x-.2,.04,z,.68);tree(x+.37,.02,z+.54,.87);
    }
    for(let j=0;j<36;j++){const z=-10+j*.59,x=main(z)-half(z)-.24;tree(x,.02,z,.48);}
    const park=group();
    for(let j=0;j<18;j++){const z=-3.9+j*.45;tree((main(z)+branch(z))/2+.12*Math.sin(j),.02,z,.53+.1*Math.cos(j),park);}
    function bridge(x0,x1,z,h,g){
        box(0x8c8c7e,(x0+x1)/2,h,z,x1-x0,.15,.35,g);
        for(const edge of [-.18,.18]){
            let prev=[x0,h+.07,z+edge];
            for(let i=1;i<=16;i++){
                const u=i/16,x=mix(x0,x1,u),rise=Math.sin(Math.PI*u)*.64;
                const next=[x,h+.07+rise,z+edge];rod(0x876d5e,prev,next,.033,g);
                if(i%2===0)rod(0x876d5e,[x,h,z+edge],next,.023,g);prev=next;
            }
        }
    }
    bridge(-4.05,.24,-2.45,.73);bridge(.24,3.72,-2.45,.73,park);
    const ship=vessel(.36);
    const marks=Array.from({length:58},(_,j)=>box(j%3?0x9ac9bf:0xc5e1cf,0,.068,0,.047,.018,.30));
    return {frame(i,s,t){
        let built=i===0||i>=7?1:i===6?smooth(s,1,10):0;
        const faces=Math.round(110*built)*6;
        side.visible=faces>0;side.geometry.setDrawRange(660-faces,faces);
        const moved=i===0||i>=5?1:i===4?smooth(s,3,11):0;
        old.visible=moved<.99;dike.visible=moved>.01;dike.scale.x=.92+.08*moved;park.visible=i===0||i>=10;
        ship.position.z=7-(t*.26)%14;ship.position.x=main(ship.position.z);ship.position.y=.065;
        for(let j=0;j<marks.length;j++){
            const m=marks[j],b=j%3===0,z=8.2-((t*.94+j*.739)%16.4);
            m.visible=!b||(built>.94&&Math.abs(z)<6.35);m.position.z=z;
            m.position.x=(b?branch(z):main(z))+Math.sin(j*2.11)*.45*(b?arch(z):half(z));
            m.rotation.y=Math.atan(((b?branch:main)(z+.01)-(b?branch:main)(z-.01))/.02);
        }
        const charts=[
            [0,0,0,13,20,18,12],[-.4,0,0,1,28,11,10],[-1,0,-2,8,10,13,10],[-1.1,0,0,1,28,6,7.6],
            [.85,0,0,1.5,27,9,9.2],[4.15,0,-.4,7,11,10,7.5],[.1,0,0,1.2,28,7,11],[.0,0,-.5,0.1,27,5,10],
            [0,0,-4,5,21,10,9],[-.3,0,-2,5,16,11,8.2],[.15,0,-1.8,10,12,12,8.7],[0,0,0,11,20,16,12.7]
        ];
        const [x,y,z,ex,ey,ez,span]=charts[i],tags=[];
        if(i===0||i===10||i===11)tags.push(['RIVER ISLAND',-.04,.28,0,24,-44]);
        if(i===1){tags.push(['NIJMEGEN',-3.6,.25,1,-80,45],['LENT',4.5,.25,1,-40,-65]);}
        if(i===3)tags.push(['NARROW REACH',main(0),.06,0,-65,-62]);
        if(i===4||i===5)tags.push([moved>.65?'NEW DIKE':'OLD DIKE',bank(0)+3.15*(moved>.65),.18,0,-110,-62]);
        if(i>=6&&i<=8&&built>.91)tags.push(['SIDE CHANNEL',branch(0),.05,0,-27,-62],['WAAL',main(0),.05,0,-90,42]);
        return {head:headings[i],note:notes[i],aim:[x,y,z],eye:[ex+.65*Math.sin(s/16),ey,ez],span:span*(1-.045*s/15),tags,diagram:i===9?'level':null,progress:s/15};
    }};
}
