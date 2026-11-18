import * as T from 'three';
import {toolkit,smooth,mix} from './waterworks.js';

export function machineKit(scene){
    const kit=toolkit(scene);
    function ring(parent,x,y,z,radius,tube,color){
        return kit.mesh(new T.TorusGeometry(radius,tube,10,72),color,x,y,z,parent);
    }
    function disk(parent,x,y,z,radius,depth,color){
        const g=new T.CylinderGeometry(radius,radius,depth,48);g.rotateX(Math.PI/2);
        return kit.mesh(g,color,x,y,z,parent);
    }
    function gear(parent,x,y,z,radius,count,color){
        const obj=kit.group(parent);obj.position.set(x,y,z);
        ring(obj,0,0,0,radius*.79,radius*.16,color);
        for(let i=0;i<count;i++){
            const a=i*Math.PI*2/count;
            kit.box(color,radius*Math.cos(a),radius*Math.sin(a),0,radius*.27,radius*.19,radius*.31,obj).rotation.z=a;
        }
        for(let i=0;i<5;i++){
            const a=i*Math.PI*2/5;kit.rod(color,[0,0,0],[radius*.74*Math.cos(a),radius*.74*Math.sin(a),0],.045,obj);
        }
        disk(obj,0,0,.02,radius*.24,.18,color);return obj;
    }
    return {...kit,ring,disk,gear};
}

export async function createFilm(canvas,film){
    const {makeModel}=await import('./'+film.episode+'-kinetic.js');
    const gl=new T.WebGLRenderer({antialias:true,preserveDrawingBuffer:true});
    gl.setSize(canvas.width,canvas.height,false);
    const scene=new T.Scene(),cam=new T.OrthographicCamera(-5,5,9,-9,.05,160);
    const space=film.episode==='webb',london=film.episode==='tower';
    const bg=space?0x14102d:london?0x7357a8:0x59b5d7;
    scene.background=new T.Color(bg);
    if(!space)scene.fog=new T.FogExp2(bg,.026);
    scene.add(new T.HemisphereLight(space?0xcad7ff:0xffeed3,space?0x372352:0x464c89,1.18));
    const key=new T.DirectionalLight(0xffe3ae,2.45);key.position.set(-5,9,7);scene.add(key);
    const rim=new T.DirectionalLight(space?0x7c54ff:0x25dcff,1.8);rim.position.set(4,4,-6);scene.add(rim);
    const fill=new T.DirectionalLight(0xff7647,.74);fill.position.set(6,3,4);scene.add(fill);
    const model=makeModel(machineKit(scene),scene);
    const ctx=canvas.getContext('2d'),W=720,H=1280,accent=space?'#ffca38':london?'#ff815c':'#b5ff53';
    function type(t,x,y,font,col,w=646){ctx.fillStyle=col;ctx.font=font;ctx.fillText(t,x,y,w);}
    function gradient(top,bottom,stops){
        const g=ctx.createLinearGradient(0,top,0,bottom);stops.forEach(([pos,color])=>g.addColorStop(pos,color));
        ctx.fillStyle=g;ctx.fillRect(0,top,W,bottom-top);
    }
    return time=>{
        const i=Math.min(11,Math.floor(time/15)),s=time-i*15;
        model.update(i,s,time);
        const shot=model.shots[i],prev=model.shots[Math.max(0,i-1)];
        const f=smooth(s,0,1.0),p=shot.map((v,j)=>mix(prev[j],v,f));
        let [x,y,z,ex,ey,ez,span]=p;
        const a=.055*Math.sin(s*Math.PI/15);
        cam.position.set(x+ex*Math.cos(a)+ez*Math.sin(a),y+ey,z+ez*Math.cos(a)-ex*Math.sin(a));
        cam.lookAt(x,y,z);span*=1-.035*Math.sin(s*Math.PI/15);
        cam.left=-span/2;cam.right=span/2;cam.top=span*H/W/2;cam.bottom=-cam.top;
        cam.updateProjectionMatrix();gl.render(scene,cam);
        ctx.setTransform(canvas.width/W,0,0,canvas.height/H,0,0);
        ctx.drawImage(gl.domElement,0,0,W,H);
        gradient(0,310,[[0,'rgba(10,15,40,.99)'],[.46,'rgba(10,15,40,.85)'],[1,'rgba(10,15,40,0)']]);
        gradient(854,H,[[0,'rgba(9,15,36,0)'],[.46,'rgba(9,15,36,.88)'],[1,'#0a1124']]);
        const reveal=smooth(s,0,.48);
        type(film.location,39,79,'bold 20px Arial',accent);
        ctx.save();ctx.globalAlpha=.55+.45*reveal;
        type(film.chapters[i],37,157+10*(1-reveal),'bold 62px Georgia','#fff5e5');ctx.restore();
        ctx.fillStyle=accent;ctx.fillRect(39,183,28+64*reveal,5);
        model.overlay?.(ctx,i,s,time);
        type(i===11?'Source  /  '+film.credit:'3D reconstruction',40,1188,'18px Arial','#c4d5df');
        ctx.fillStyle='#405069';ctx.fillRect(40,1209,640,3);ctx.fillStyle=accent;ctx.fillRect(40,1209,time/180*640,3);
    };
}

export function panel(c,title,paint){
    c.save();c.fillStyle='rgba(13,25,53,.94)';c.fillRect(39,764,642,160);
    c.fillStyle='#56dcf2';c.fillRect(39,764,5,160);c.fillStyle='#e0f8f8';c.font='bold 19px Arial';c.fillText(title,60,797,596);
    paint(c);c.restore();
}
export function annotation(c,label,detail,color='#9df4eb'){
    c.save();c.fillStyle='rgba(12,27,48,.90)';c.fillRect(39,864,642,64);
    c.fillStyle=color;c.font='bold 24px Arial';c.fillText(label,58,904,262);
    c.fillStyle='#fff4df';c.font='21px Arial';c.fillText(detail,324,904,335);c.restore();
}
