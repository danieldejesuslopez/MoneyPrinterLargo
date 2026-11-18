import * as T from 'three';
import {smooth,mix,palette as p} from './waterworks.js';
const heads=['A ship climbs inland','Three steps up','Close the gates','Two water levels','Under the walls','Under the hull','The ship rises','Level with the next lock','Across Gatun Lake','Down to the Pacific','Emptying the chamber','From ocean to ocean'];
const notes=['The original Panama Canal locks','Atlantic side  /  Gatun Locks','Two gate leaves meet in a shallow V','Higher water waits beyond the gate','Gravity drives the flow','100 floor openings in each original chamber','The deck rises against a fixed wall','Equal levels allow the gates to open','About 26 m above sea level','Pedro Miguel: one step  /  Miraflores: two','Water drains towards the lower level','Source: Panama Canal Authority'];
export function makeLocks({group,mesh,box,rod,ribbon,tree,vessel}){
    const walls=group(),near=group(),pipes=group(),dots=group();
    box(0xb3a585,0,-1.59,0,32,.85,43);
    for(const side of [-1,1]){
        box(0x809471,side*7,-.44,0,10.5,1.84,36,walls);
        for(let j=0;j<22;j++)tree(side*(3.55+(j%3)*.62),.48,-13+j*1.26,.75+j%3*.12,walls);
    }
    for(let k=-1;k<=1;k++){
        const z=-k*5.6,base=.25+k*.75;
        for(const side of [-1,1]){
            const parent=side===1?near:walls;
            box(0xa39f91,side*1.52,base+.11,z,.55,2.68,5.57,parent);
            box(0xd6cdb5,side*1.52,base+1.49,z,.66,.12,5.57,parent);
            for(let j=0;j<9;j++)box(0x655e52,side*1.52,base+1.58,z-2.44+j*.59,.075,.08,.1,parent);
        }
    }
    const water=box(p.water,0,0,0,2.5,.10,5.57),far=box(p.water,0,0,-6.8,2.50,.10,8),low=box(p.water,0,0,7.4,2.50,.10,9.2);
    const gates=[];
    for(const z of [-2.8,2.8])for(const side of [-1,1]){
        const g=group();g.position.set(side*1.29,-.15,z);gates.push([g,side,z]);
        const x=-side*.696;
        box(0x6b6055,x,.42,0,1.41,2.03,.16,g);
        for(let n=0;n<6;n++)box(0xaa8b62,x,-.46+n*.34,.09,1.39,.053,.074,g);
        rod(0xc29468,[-side*.05,-.53,.10],[-side*1.33,1.37,.10],.047,g);
    }
    const ship=vessel(.83);
    // Wall culvert, transverse branches and five openings on each of twenty rows.
    rod(0xc58649,[1.47,-.88,-3.6],[1.47,-.88,3.3],.17,pipes);
    for(let k=0;k<20;k++){
        const z=-2.41+k*.253;rod(0xb38553,[-1.06,-.88,z],[1.47,-.88,z],.053,pipes);
        for(let n=0;n<5;n++)rod(0xa7c8ab,[-1.04+n*.51,-.90,z],[-1.04+n*.51,-.58,z],.043,pipes);
    }
    const glow=new T.SphereGeometry(.046,6,5);
    const jets=Array.from({length:50},()=>mesh(glow,0x9ce8e1,0,0,0,dots));
    const gauge=group();box(0xeedbb0,-1.19,.39,.09,.09,2.65,.20,gauge);
    for(let k=0;k<12;k++)box(0x525b52,-1.13,-.76+k*.19,.09,.05,.038,.16,gauge);
    const marks=Array.from({length:24},(_,j)=>box(0x96c6c1,0,0,0,.035,.02,.23));
    return {frame(i,s,t){
        const rising=i===6?smooth(s,1,12):i===7||i===8||i===9?1:i===10?1-smooth(s,.5,13):0;
        let level=mix(-.36,.79,rising);
        if(i===0)level=mix(-.36,.79,smooth(s,2,12));
        const cut=i===4||i===5||i===10;
        near.visible=!cut&&i!==6;walls.visible=!cut;pipes.visible=cut;dots.visible=cut;water.visible=!cut;gauge.visible=i===6||i===7;
        water.position.y=level;far.position.y=.79;low.position.y=-.36;
        for(const [g,side,z] of gates){
            let open=z>0?(i===2?1-smooth(s,4,12):i<=1?1:0):(i===7?smooth(s,3,11):i===8?1:0);
            g.rotation.y=side*mix(-.37,-Math.PI/2,open);
        }
        let shipZ=.20;if(i===2)shipZ=mix(3.15,.10,smooth(s,0,5));if(i===7)shipZ=mix(.1,-2.4,smooth(s,7,15));
        if(i===8)shipZ=-2.9-s*.25;
        ship.position.set(0,level+.07,shipZ);
        for(let j=0;j<jets.length;j++){
            const n=j%5,k=Math.floor(j/5),u=(t*.8+j*.17)%1;
            const z=-2.30+k*.49;
            let x=mix(1.45,-1.04+n*.51,Math.min(1,u/.74)),y=-.87+.83*smooth(u,.74,1);
            if(i===10){x=mix(-1.04+n*.51,1.45,smooth(u,.25,1));y=mix(-.04,-.87,smooth(u,0,.25));}
            jets[j].position.set(x,y,z);
        }
        for(let j=0;j<marks.length;j++){const m=marks[j],z=8.5-(t*.42+j*.731)%17;
            m.position.set(Math.sin(j*37)*1.11,(z<-2.8?.79:z>2.8?-.36:level)+.06,z);
            m.visible=!cut&&(Math.abs(z-shipZ)>1.45||Math.abs(m.position.x)>.55);}
        const tags=[];
        if(i===2)tags.push(['MITER GATES',.50,.9,2.8,-113,-58]);
        if(i===3)tags.push(['HIGHER LEVEL',.45,.85,-3.5,2,-54],['ENTRY LEVEL',.44,-.25,1.9,-80,43]);
        if(i===4||i===5||i===10)tags.push([i===5?'FLOOR OPENINGS':'WALL CULVERT',i===5?.05:1.45,-.64,0,36,-49]);
        if(i===6||i===7)tags.push(['FIXED WALL',-1.5,1.8,0,-90,-40]);
        const charts=[[0,.2,0,8,9,12,8],[0,.2,-1,4,15,11,10],[0,.3,1.7,1,12,7,6.4],[0,.24,-1.1,7,7,7,6.8],
            [0,-.20,0,7,7,5,5.3],[0,-.20,0,4,11,3,5.0],[0,.38,0,3,8,5,5.45],[0,.38,-1.2,5,8,6,6.4],
            [0,.55,-3.4,6,12,9,10],[0,.25,0,5,14,9,11],[0,-.1,0,6,8,7,5.8],[0,.2,-1,7,14,10,11]];
        const [x,y,z,ex,ey,ez,span]=charts[i];
        return {head:heads[i],note:notes[i],aim:[x,y,z],eye:[ex+.4*Math.sin(s/15),ey,ez],span:span*(1-.035*s/15),tags,
            diagram:[0,1,8,9,11].includes(i)?'canal':null,progress:(i===0?.06:i===1?.18:i===8?.45:i===9?.76:.93)+s/270};
    }};
}
