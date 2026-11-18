import * as T from 'three';

const TAU = Math.PI * 2;
const clamp = x => Math.max(0, Math.min(1,x));
function ease(t,a,b) { const u=clamp((t-a)/(b-a)); return u*u*(3-2*u); }
function rng(seed) { return () => ((seed=(Math.imul(seed,1664525)+1013904223)>>>0)/4294967296); }

// A tiny handmade observatory garden, lit like a tabletop puppet set.
export function createFilm(canvas) {
    const random=rng(814), w=canvas.width, h=canvas.height;
    const renderer=new T.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true});
    renderer.setSize(w,h,false);
    const scene=new T.Scene(), cam=new T.PerspectiveCamera(35,w/h,0.1,100);
    const ctx=canvas.getContext('2d');
    const materials=new Map();
    function material(color) {
        if (!materials.has(color)) materials.set(color,new T.MeshStandardMaterial({color,roughness:.65,metalness:.13}));
        return materials.get(color);
    }
    function group(parent,x=0,y=0,z=0) {
        const g=new T.Group(); parent.add(g); g.position.set(x,y,z); return g;
    }
    const sphere=new T.SphereGeometry(1,28,18), rock=new T.IcosahedronGeometry(1,0);
    function shape(parent,geo,color,x,y,z,sx=1,sy=sx,sz=sx) {
        const m=new T.Mesh(geo,material(color)); parent.add(m); m.position.set(x,y,z); m.scale.set(sx,sy,sz); return m;
    }
    function egg(p,c,x,y,z,a,b,d) { return shape(p,sphere,c,x,y,z,a,b,d); }
    function rod(p,c,a,b,r,top=r) {
        const start=new T.Vector3(...a), end=new T.Vector3(...b), v=end.clone().sub(start);
        const m=shape(p,new T.CylinderGeometry(top,r,v.length(),16),c,0,0,0);
        m.position.copy(start).lerp(end,.5); m.quaternion.setFromUnitVectors(new T.Vector3(0,1,0),v.normalize()); return m;
    }
    scene.add(new T.HemisphereLight(0xffefd9,0x9b87b6,1.8));
    const sun=new T.DirectionalLight(0xffdfae,2.7); sun.position.set(-4,7,4); scene.add(sun);
    const rim=new T.DirectionalLight(0xffa6a1,2); rim.position.set(3,4,-5); scene.add(rim);
    const island=group(scene);
    // Hand-cut strata share a silhouette but break into differently colored facets.
    const n=27, edges=Array.from({length:n},()=>.93+random()*.12);
    const layers=[[0,1],[-.34,.99],[-.63,.90],[-1.15,.75],[-1.85,.46],[-2.5,.08]];
    const strata=['#b19172','#cd9c78','#836675','#67566c','#554c66'];
    for(let j=0;j<layers.length-1;j++){
        const positions=[], colors=[];
        function point(i,k) {
            const a=i*TAU/n, [y,r]=layers[k];
            return [Math.cos(a)*4.8*r*edges[i%n],y+Math.sin(3*a+k*.7)*.10*k,Math.sin(a)*2.8*r*edges[i%n]];
        }
        for(let i=0;i<n;i++){
            const A=point(i,j), B=point(i+1,j), C=point(i,j+1), D=point(i+1,j+1);
            for(const tri of [[A,B,C],[B,D,C]]) {
                const color=new T.Color(strata[j]).multiplyScalar(.78+random()*.35);
                for(const v of tri){positions.push(...v); colors.push(color.r,color.g,color.b);}
            }
        }
        const geo=new T.BufferGeometry(); geo.setAttribute('position',new T.Float32BufferAttribute(positions,3));
        geo.setAttribute('color',new T.Float32BufferAttribute(colors,3)); geo.computeVertexNormals();
        island.add(new T.Mesh(geo,new T.MeshStandardMaterial({vertexColors:true,side:T.DoubleSide})));
    }
    egg(island,'#969372',0,-.09,0,4.58,.23,2.68);
    for(let i=0;i<55;i++){
        const a=random()*TAU,r=Math.sqrt(random()),x=Math.cos(a)*4.4*r,z=Math.sin(a)*2.5*r;
        if(Math.abs(x)<2 && Math.abs(z)<1) continue;
        const s=.05+random()**2*.36;
        shape(island,rock,['#74687c','#dbb290','#b4aa92'][i%3],x,.08,z,s,s*.67,s*.84).rotation.y=a;
    }
    const brass='#d99344', shell='#eab458', dark='#354759', trim='#875740', cream='#f2d199';
    const bot=group(island), body=group(bot,0,.56,0), head=group(body,0,.62,0);
    const legs=[], arms=[], eyes=[];
    for(const sign of [-1,1]){
        const leg=group(bot,sign*.22,.54,0); legs.push(leg);
        rod(leg,dark,[0,0,0],[0,-.29,0],.071);
        egg(leg,trim,0,-.32,.07,.19,.125,.25);
        egg(leg,brass,0,-.29,.12,.178,.088,.21);
    }
    egg(body,dark,0,.18,0,.395,.39,.295);
    egg(body,brass,0,.18,.015,.39,.31,.30);
    egg(body,cream,0,.22,.29,.13,.115,.035);
    egg(body,'#369b94',0,.22,.324,.077,.071,.015);
    egg(head,shell,0,.12,0,.568,.425,.40);
    egg(head,trim,0,.12,.258,.49,.30,.17);
    egg(head,dark,0,.13,.286,.467,.27,.167);
    for(const sign of [-1,1]){
        egg(head,trim,sign*.54,.12,0,.09,.145,.14);
        const eye=egg(head,'#a6ffda',sign*.183,.155,.435,.078,.095,.029);
        eye.material.emissive.setHex(0x3d977d); eyes.push(eye);
        const arm=group(body,sign*.38,.37,0); arms.push(arm);
        egg(arm,cream,0,0,0,.108,.11,.105);
        rod(arm,dark,[0,-.04,0],[sign*.08,-.28,.04],.062);
        egg(arm,brass,sign*.08,-.31,.04,.108,.135,.102);
    }
    rod(head,trim,[.12,.46,0],[.20,.75,0],.028);
    egg(head,'#ee775d',.20,.76,0,.085,.085,.085);
    // Soft contact shadows anchor the feet even as the character moves.
    const shade=egg(island,'#706e58',0,.143,0,.48,.012,.25);
    const seed=egg(island,'#f7c452',.9,.29,.24,.14,.24,.14);
    seed.material.emissive.setHex(0x9a5825);
    const drop=egg(island,'#70cedf',0,0,0,.073,.12,.073);
    function flower(x,z,size,color,delay){
        const root=group(island,x,.12,z);
        const stalk=group(root);
        rod(stalk,'#528579',[0,0,0],[.12,1.15,0],.044,.026);
        for(const sign of [-1,1]){
            const leaf=egg(stalk,'#68a38b',sign*.15,.36+(sign+1)*.13,0,.13,.25,.048);
            leaf.rotation.z=-sign*.82;
        }
        const bloom=group(stalk,.12,1.16,0); bloom.rotation.x=.72;
        const petals=[];
        for(let i=0;i<9;i++){
            const hinge=group(bloom); hinge.rotation.z=i*TAU/9;
            const petal=group(hinge); petals.push(petal);
            egg(petal,color,0,.198,0,.12,.23,.061);
        }
        egg(bloom,'#eab054',0,0,.08,.155,.155,.10);
        for(let i=0;i<13;i++){
            const a=i*2.4,r=Math.sqrt(i/14)*.12;
            egg(bloom,'#965b37',r*Math.cos(a),r*Math.sin(a),.164-r*.30,.014,.014,.012);
        }
        return t=>{
            const g=ease(t,delay,delay+3.1);
            root.visible=g>0; if(!root.visible)return;
            root.scale.setScalar(size*g); stalk.rotation.z=.055*Math.sin(t*1.4+x);
            for(const p of petals)p.rotation.x=1.3*(1-ease(t,delay+2,delay+4.6));
        };
    }
    const blooms=[flower(.90,.24,2.15,'#f37f71',38.2)];
    for(let i=0;i<40;i++){
        const a=i*2.40,r=Math.sqrt((i+.5)/40);
        const x=Math.cos(a)*4.22*r,z=Math.sin(a)*2.3*r;
        if(Math.abs(x+1.1)<.75 && Math.abs(z)<.75)continue;
        blooms.push(flower(x,z,.32+random()*.47,['#eca38c','#f2c86b','#a49bc9','#ef836d'][i%4],44+5*r+random()*1.7));
    }
    const sky=document.createElement('canvas'); sky.width=w; sky.height=h;
    const bg=sky.getContext('2d');
    const gradient=bg.createLinearGradient(0,0,w*.27,h);
    for(const [s,c] of [[0,'#7299b1'],[.43,'#b1c6cb'],[.73,'#f2cfb7'],[1,'#dfa6a2']])gradient.addColorStop(s,c);
    bg.fillStyle=gradient; bg.fillRect(0,0,w,h);
    const clouds=Array.from({length:32},()=>[random(),random(),.03+random()*.13,.012+random()*.025]);
    const pollen=Array.from({length:36},()=>[random(),random(),random()]);
    const shots=[
        [0, [-3,3.3,12],[1,.22,0],[-1,2.85,10],[.8,.3,0]],
        [9, [-2.2,1.75,4.6],[-1.42,1.03,0],[-1.8,1.6,3.8],[-1.15,1,0]],
        [19, [2.6,1.1,4.3],[.45,.56,.1],[2.05,.95,3.45],[.46,.57,.1]],
        [29, [.7,2.2,5.5],[0,.83,0],[1.4,1.8,4.7],[.24,.78,0]],
        [38, [2.1,1.15,4.5],[.83,.76,.2],[2.6,2.2,6.55],[.73,1.85,0]],
        [45.8, [-3.7,2.8,6.9],[.1,.97,0],[-2.3,2.5,7.9],[.03,.97,0]],
        [52, [2.2,2.65,8.7],[.05,.67,0],[3.8,3.2,12.6],[0,.22,0]]
    ];
    const pos=new T.Vector3(), look=new T.Vector3();
    function draw(t){
        const k=shots.findLastIndex(s=>s[0]<=t), [start,p1,l1,p2,l2]=shots[k];
        const u=ease(t,start,shots[k+1]?.[0]??60);
        cam.position.fromArray(p1).lerp(pos.fromArray(p2),u);
        cam.lookAt(look.fromArray(l1).lerp(pos.fromArray(l2),u));
        const walk=ease(t,9.4,18.8), step=Math.sin(t*7.3)*Math.sin(Math.PI*walk);
        bot.position.set(-2.35+1.93*walk,.045+Math.abs(step)*.078,.04);
        bot.rotation.y=.38+.65*ease(t,13,20)-.66*ease(t,48,55);
        shade.position.x=bot.position.x;
        legs.forEach((leg,i)=>leg.rotation.x=(i?1:-1)*step*.68);
        const lean=ease(t,21,24)-ease(t,36,39);
        body.rotation.z=-.25*lean+.025*Math.sin(t*2);
        head.rotation.z=.14*Math.sin(t*1.5)+lean*.2;
        head.rotation.x=.2*lean-.15*ease(t,38,43);
        arms[0].rotation.x=.45*step;
        arms[1].rotation.z=.16+1.22*(ease(t,29.3,32)-ease(t,36,38))+.9*ease(t,51,54);
        arms[1].rotation.x=-.55*step-.3;
        const blink=1-.93*Math.pow(Math.max(0,Math.cos(t*2.16)),82);
        eyes.forEach(e=>e.scale.y=.095*blink);
        seed.position.y=.28+.07*Math.sin(t*3); seed.rotation.z=.28*Math.sin(t*2);
        seed.visible=t<40;
        const fall=ease(t,33.2,34.4); drop.visible=t>=32.6&&t<34.4;
        drop.position.set(.32+.56*fall,1.15-.87*fall,.25);
        blooms.forEach(update=>update(t));
        renderer.render(scene,cam);
        ctx.drawImage(sky,0,0);
        ctx.fillStyle='rgba(255,236,202,.83)';ctx.beginPath();ctx.arc(w*.80,h*.23,h*.095,0,TAU);ctx.fill();
        for(const [x,y,rx,ry] of clouds){
            const cx=((x+t*.0011)%1.24-.12)*w, cy=(.24+y*.88)*h;
            ctx.save(); ctx.translate(cx,cy);ctx.scale(rx*w,ry*h);
            const halo=ctx.createRadialGradient(0,0,0,0,0,1);
            halo.addColorStop(0,'rgba(255,246,223,.54)');halo.addColorStop(1,'rgba(255,250,237,0)');
            ctx.fillStyle=halo;ctx.fillRect(-1,-1,2,2);ctx.restore();
        }
        ctx.drawImage(renderer.domElement,0,0);
        ctx.fillStyle='rgba(255,233,159,.8)';
        for(const [x,y,s] of pollen){
            const px=((x+.05*Math.sin(t*.3+y*8)+1)%1)*w, py=((y-t*(.007+.006*s)+2)%1)*h;
            ctx.globalAlpha=(.12+.55*ease(t,40,46))*(.6+.4*Math.sin(t+x*8));
            ctx.beginPath();ctx.arc(px,py,(1+2*s)*h/720,0,TAU);ctx.fill();
        }
        ctx.globalAlpha=1;
        if(t<8){
            ctx.save();ctx.globalAlpha=(1-ease(t,6,8))*(.7+.3*ease(t,0,.65));
            ctx.fillStyle='#263f50';ctx.font='italic bold '+Math.round(h*.080)+'px Georgia';
            ctx.fillText('El jardín de latón',w*.064,h*.176);ctx.restore();
        }
    }
    return draw;
}
