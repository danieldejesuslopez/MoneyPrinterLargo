import * as T from 'three';
import {smooth,mix,palette as p} from './waterworks.js';

const heads=['A wall beneath a ship','A river with two flows','When a surge arrives','Ten gates at Woolwich','Watching the tide','The decision to close','Four hours to the peak','Inside a rising gate','Holding back the tide','Waiting for equal levels','The river drains','An open river again'];
const notes=['The Thames Barrier','City upstream  /  Sea downstream','Storm surge adds to the tide','520 metres across the Thames','Gauges and forecasts feed the control room','Tide height, surge and river flow','Typical surge closure near low tide','Main navigation gate  /  cutaway','The river keeps arriving from inland','Downstream falls to upstream level','Outflow resumes on the ebb tide','Managed by the Environment Agency'];
export function makeBarrier({group,mesh,box,rod,tree,house,vessel}){
    const riverbed=box(0x746e59,0,-.80,0,34,.35,40);
    const banks=group();
    for(const sign of [-1,1]){
        box(0xafb293,sign*13,.03,0,8,1.35,44,banks);box(0x9c917e,sign*9.02,.05,0,.22,1.5,44,banks);
        for(let k=0;k<17;k++){
            const z=-12+k*1.45,x=sign*(9.8+(k%3)*.54);
            house(x,.71,z,1.2,0xb49178,banks);tree(sign*9.48,.72,z+.44,.68,banks);
        }
    }
    const surfaces=[-1,1].map(sign=>box(p.water,0,0,sign*10,18,.11,20));
    const marks=Array.from({length:50},(_,j)=>box(j%3?0x98cfcc:0xe6ddba,0,0,0,.065,.018,.42));
    const pier=group(),gates=[];
    const widths=[.8,.8,1.44,2.47,2.47,2.47,2.47,1.44,.8,.8];
    const total=widths.reduce((a,b)=>a+b,0);let left=-total/2;
    const axis=-.04;
    function pierAt(x){
        box(0x8d9086,x,.52,0,.27,1.9,1.84,pier);
        for(const sign of [-1,1]){
            const hood=mesh(new T.SphereGeometry(1,16,12),0xa2adab,x,1.37,sign*.38,pier);
            hood.scale.set(.22,.59,.71);hood.rotation.x=-sign*.29;
            rod(0x6e8283,[x,1.82,sign*.24],[x,1.5,sign*.95],.041,pier);
        }
    }
    widths.forEach((w,j)=>{
        pierAt(left);const g=group();g.position.set(left+w/2,axis,0);gates.push(g);
        if(j>=2&&j<=7){
            // Radial cross-section of the main gate, drawn around the trunnion axis.
            const shape=new T.Shape();const R=1.02;
            shape.moveTo(0,0);shape.absarc(0,0,R,-Math.PI/2,0,false);shape.lineTo(0,0);
            const arc=new T.ExtrudeGeometry(shape,{depth:w-.22,bevelEnabled:false,curveSegments:15});
            // local shape x maps to world z, shape y to y, extrusion along x
            arc.rotateY(-Math.PI/2);mesh(arc,0xa5734f,(w-.22)/2,0,0,g);
            for(let k=0;k<6;k++){const a=-Math.PI/2+k*Math.PI/10;
                box(0xe2b57d,0,R*Math.sin(a),R*Math.cos(a),w-.19,.045,.05,g);}
        }else{box(0x9c8971,0,.64,0,w-.18,1.28,.13,g);}
        left+=w;
    });pierAt(left);
    const ship=vessel(.82);
    const gauge=group();for(let k=0;k<2;k++){const z=k?2:-2;
        box(0xefe5ca,1.80,.84,z,.14,2.25,.12,gauge);
        for(let j=0;j<10;j++)box(0x35494a,1.80,.04+j*.20,z+.068,.13,.043,.012,gauge);
    }
    return {frame(i,s,t){
        const phase=i<=6?0:i===7?smooth(s,.8,10.7):i<10?1:i===10?1-smooth(s,1.5,8):0;
        let sea=.27,city=.27;
        if(i===2){sea+=.61*smooth(s,1,12);city=sea;}
        if(i===8){sea+=.60*smooth(s,0,10);city+=.18*smooth(s,1,13);}
        if(i===9){sea+=mix(.60,.18,smooth(s,1,13));city+=.18;}
        if(i===10){sea+=.18*(1-smooth(s,2,11));city=sea;}
        surfaces[0].position.y=sea;surfaces[1].position.y=city;
        const cut=i===7;surfaces.forEach(o=>o.visible=!cut);
        for(let j=0;j<gates.length;j++){const g=gates[j];if(j>=2&&j<=7)g.rotation.x=-phase*Math.PI*.49;else g.position.y=1.8-phase*1.8;}
        gauge.visible=i>=8&&i<=10;
        ship.visible=i===0||i===1||i===3||i===11;
        ship.position.set(1.24,city+.075,6.2-s*.74);
        for(let j=0;j<marks.length;j++){
            const zSign=j%2?1:-1,incoming=[1,2,8,9].includes(i);
            const direction=zSign>0||incoming?-1:1;
            const z=zSign*(.27+((j*.921+direction*t*.83)%11+11)%11);
            const m=marks[j];m.visible=!cut;m.position.set(-8.5+((j*2.734)%17),(zSign>0?city:sea)+.068,z);
        }
        const tags=[];
        if(i===0||i===3||i===11)tags.push(['NAVIGATION SPAN',1.22,.4,0,35,-62]);
        if(i===1||i===2||i===8||i===9||i===10){
            tags.push(['SEA SIDE',.15,sea,-3.8,15,-48],['LONDON SIDE',.15,city,3.8,-125,35]);}
        if(i===7)tags.push(['CURVED STEEL GATE',1.2,.28+phase*.58,0,-142,-84]);
        const charts=[[0,0,0,9,9,15,12],[0,0,0,3,21,12,17],[0,0,-3,8,11,13,13],[0,0,0,3,18,17,21],
            [0,0,-2,6,17,18,16],[0,0,0,3,20,13,15],[0,0,0,5,12,18,13],[1.15,0,0,4,4.8,6,4.9],
            [.6,0,0,8,8,6,10],[.85,.2,0,9,8,5,8.2],[.7,0,0,6,13,10,12],[0,0,0,7,12,17,14]];
        const [x,y,z,ex,ey,ez,span]=charts[i];
        return {head:heads[i],note:notes[i],aim:[x,y,z],eye:[ex+.47*Math.sin(s/14),ey,ez],span:span*(1-.035*s/15),tags,
            diagram:i===4?'forecast':i===5?'decision':i===6?'tide':null,progress:s/15};
    }};
}
