import io
import json
from unittest.mock import patch
import unittest

from PIL import Image, ImageChops

from app.image_tools import OutpaintParams, geometry, compose_preserved
from app.seam_blend import build_seam_mask, blend_outpaint_seams, verify_core
from tests import test_image_tools as helpers


def fixture():
    original = Image.new('RGB',(128,128))
    original.putdata([(60+x//3,80+y//3,100+(x+y)//6) for y in range(128) for x in range(128)])
    params = OutpaintParams(extend_left=32,extend_right=32,extend_top=32,extend_bottom=32,
                           seam_mode='feather',prompt='extend',seed=42).model_dump()
    canvas = geometry(original.size,params)
    candidate = Image.new('RGB',(192,192))
    candidate.putdata([(60+(x-32)//3+12,80+(y-32)//3+12,100+(x+y-64)//6+12)
                       for y in range(192) for x in range(192)])
    return original,candidate,canvas,params


class SeamTests(unittest.TestCase):
    def test_direction_geometry_alignment_and_bad_values(self):
        for side in ('left','right','top','bottom'):
            for size in ((256,256),(255,255),(257,259)):
                params = {f'extend_{k}': int(k==side) for k in ('left','right','top','bottom')}
                g = geometry(size,params)
                self.assertEqual(g['offset_x'],params['extend_left'])
                self.assertEqual(g['offset_y'],params['extend_top'])
                self.assertEqual(g['width'],size[0]+g['actual_margins']['left']+g['actual_margins']['right'])
                self.assertEqual(g['height'],size[1]+g['actual_margins']['top']+g['actual_margins']['bottom'])
                self.assertEqual(g['width']%32,0)
        for size in ((0,256),(4096,4096)):
            with self.assertRaises(ValueError):
                geometry(size,OutpaintParams().model_dump())
        with self.assertRaises(ValueError):
            geometry((256,256),dict(extend_left=-1,extend_right=2,extend_top=0,extend_bottom=0))

    def test_mask_inactive_edges_corners_and_core(self):
        mask,width=build_seam_mask((128,128),dict(left=32,right=0,top=0,bottom=0),32)
        self.assertEqual(width,32)
        self.assertEqual(mask.getpixel((0,64)),0)
        self.assertEqual(mask.getpixel((64,0)),255)
        self.assertEqual(mask.getpixel((127,64)),255)
        four,_=build_seam_mask((128,128),dict.fromkeys(('left','right','top','bottom'),32),32)
        self.assertEqual(four.getpixel((16,16)),four.getpixel((16,64)))
        self.assertEqual(four.getpixel((64,64)),255)
        self.assertEqual(four.getpixel((127,127)),0)

    def test_strict_unchanged_feather_core_and_better_boundary(self):
        original,candidate,g,params=fixture()
        base=compose_preserved(original,candidate,g)
        strict,info,_=blend_outpaint_seams(original,candidate,g,params|{'seam_mode':'strict'})
        self.assertIsNone(ImageChops.difference(base,strict).getbbox())
        result,info,mask=blend_outpaint_seams(original,candidate,g,params)
        self.assertEqual(info['effective_mode'],'feather')
        self.assertTrue(info['tone']['applied'])
        self.assertLess(info['after']['max_jump'],info['before']['max_jump'])
        self.assertEqual(verify_core(original,result,g,mask)['different_pixels'],0)
        result.putpixel((96,96),(255,255,255))
        with self.assertRaisesRegex(ValueError,'核心像素'):
            verify_core(original,result,g,mask)

    def test_tone_disabled_flat_skipped_and_malformed_rejected(self):
        original,candidate,g,params=fixture()
        with patch('app.seam_blend._local_tone',side_effect=AssertionError('must not run')):
            blend_outpaint_seams(original,candidate,g,params|{'tone_match':False})
        _,info,_=blend_outpaint_seams(Image.new('RGB',original.size,'white'),candidate,g,params)
        self.assertFalse(info['tone']['applied'])
        for bad in (Image.new('RGB',(10,10)),candidate.convert('L')):
            with self.assertRaises(ValueError):
                blend_outpaint_seams(original,bad,g,params)

    def test_tone_regression_falls_back_without_changing_source(self):
        original,candidate,g,params=fixture()
        raw=original.tobytes()
        bad=candidate.copy()
        for y in range(32,160):
            bad.putpixel((31,y),(255,255,255))
        with patch('app.seam_blend._local_tone',return_value=(bad,{'applied':True})):
            _,info,_=blend_outpaint_seams(original,candidate,g,params)
        self.assertFalse(info['tone']['applied'])
        self.assertTrue(info['fallback'])
        self.assertEqual(original.tobytes(),raw)

    def test_tiny_image_reverts_to_strict(self):
        original=Image.new('RGB',(1,1))
        params=OutpaintParams(seam_mode='feather').model_dump()
        g=geometry(original.size,params)
        _,info,mask=blend_outpaint_seams(original,Image.new('RGB',(g['width'],g['height'])),g,params)
        self.assertEqual(info['effective_mode'],'strict')
        self.assertTrue(info['fallback'])
        self.assertEqual(mask.getpixel((0,0)),255)

    def test_tone_samples_generated_band_on_bright_scene(self):
        # Means near 120/140/145: the old sum-of-means bound skipped almost every real photo.
        original=Image.new('RGB',(128,128))
        original.putdata([(90+x//2,110+y//2,130+(x+y)//8) for y in range(128) for x in range(128)])
        params=OutpaintParams(extend_left=32,extend_right=0,extend_top=0,extend_bottom=0,
                              seam_mode='feather',prompt='extend',seed=7).model_dump()
        g=geometry(original.size,params)
        candidate=Image.new('RGB',(g['width'],g['height']))
        candidate.paste(original,(g['offset_x'],g['offset_y']))
        for cx in range(g['offset_x']):
            for cy in range(128):
                r,gg,b=original.getpixel((0,cy))
                candidate.putpixel((cx,cy),(min(255,r+20),min(255,gg+20),min(255,b+20)))
        result,info,mask=blend_outpaint_seams(original,candidate,g,params)
        self.assertTrue(info['tone']['applied'],info['tone'])
        self.assertTrue(all(v<0 for v in info['tone']['offset_rgb']),info['tone'])
        self.assertLess(info['after']['max_jump'],info['before']['max_jump'])
        self.assertEqual(verify_core(original,result,g,mask)['different_pixels'],0)

    def test_each_direction_blends_only_its_own_edge(self):
        for side in ('left','right','top','bottom'):
            original=Image.new('RGB',(96,96))
            original.putdata([(50+x//2,70+y//2,90+(x+y)//6) for y in range(96) for x in range(96)])
            sizes={k:(32 if k==side else 0) for k in ('left','right','top','bottom')}
            params=OutpaintParams(extend_left=sizes['left'],extend_right=sizes['right'],
                                  extend_top=sizes['top'],extend_bottom=sizes['bottom'],
                                  seam_mode='feather',prompt='extend',seed=3).model_dump()
            g=geometry(original.size,params)
            candidate=Image.new('RGB',(g['width'],g['height']))
            candidate.paste(Image.eval(original,lambda v:min(255,v+3)),(g['offset_x'],g['offset_y']))
            for cx in range(g['width']):
                for cy in range(g['height']):
                    if g['offset_x']<=cx<g['offset_x']+original.width and g['offset_y']<=cy<g['offset_y']+original.height:
                        continue
                    r,gg,b=original.getpixel((min(max(cx-g['offset_x'],0),original.width-1),
                                              min(max(cy-g['offset_y'],0),original.height-1)))
                    candidate.putpixel((cx,cy),(min(255,r+10),min(255,gg+10),min(255,b+10)))
            strict=compose_preserved(original,candidate,g)
            result,info,mask=blend_outpaint_seams(original,candidate,g,params|{'tone_match':False})
            self.assertEqual(info['effective_mode'],'feather',side)
            x,y=g['offset_x'],g['offset_y']
            strips={'left':(x,y+32,x+8,y+original.height-32),'right':(x+original.width-8,y+32,x+original.width,y+original.height-32),
                    'top':(x+32,y,x+original.width-32,y+8),'bottom':(x+32,y+original.height-8,x+original.width-32,y+original.height)}
            for other,box in strips.items():
                if other==side: continue
                self.assertIsNone(ImageChops.difference(strict.crop(box),result.crop(box)).getbbox(),f'{side} changed {other}')
            self.assertEqual(verify_core(original,result,g,mask)['different_pixels'],0)
            changed=ImageChops.difference(strict,result)
            self.assertIsNotNone(changed.getbbox(),side)

    def test_corners_are_not_double_weighted(self):
        original=Image.new('RGB',(96,96))
        original.putdata([(60+x//3,80+y//3,120) for y in range(96) for x in range(96)])
        params=OutpaintParams(extend_left=32,extend_right=32,extend_top=32,extend_bottom=32,
                              seam_mode='feather',prompt='extend',seed=5).model_dump()
        g=geometry(original.size,params)
        candidate=Image.new('RGB',(g['width'],g['height']),(180,180,180))
        candidate.paste(original,(g['offset_x'],g['offset_y']))
        result,info,_=blend_outpaint_seams(original,candidate,g,params|{'tone_match':False})
        self.assertEqual(info['effective_mode'],'feather')
        w,h=g['width'],g['height']
        for point in ((0,0),(w-1,0),(0,h-1),(w-1,h-1)):
            self.assertEqual(result.getpixel(point),candidate.getpixel(point),point)
        # Corners fade toward the candidate; they must not be darker/lighter than either input.
        for point in ((0,0),(w-1,0),(0,h-1),(w-1,h-1)):
            values=result.getpixel(point)
            self.assertTrue(all(min(60,180)<=v<=max(60,180) for v in values),point)


# Reuse setup helpers, not the inherited test suite.
class SeamJobTests(unittest.TestCase):
    setUp=helpers.ImageToolTests.setUp
    tearDown=helpers.ImageToolTests.tearDown
    post=helpers.ImageToolTests.post
    upload=helpers.ImageToolTests.upload
    wait_job=helpers.ImageToolTests.wait_job
    available=helpers.ImageToolTests.available
    request=helpers.ImageToolTests.request

    def feather_job(self):
        original,candidate,_,params=fixture()
        # API minimum target 256: a 128 source plus 64 padding on every side.
        params.update(extend_left=64,extend_right=64,extend_top=64,extend_bottom=64)
        stream=io.BytesIO(); original.save(stream,'PNG')
        item=self.post('/inputs',files={'file':('fixture.png',stream.getvalue(),'image/png')}).json()
        with self.available(),patch.object(self.tools,'run_outpaint',return_value=(candidate.resize((256,256)),{'method':'test-fixture'})):
            response=self.request(item['id'],'outpaint',params)
            self.assertEqual(response.status_code,202,response.text)
            job=self.wait_job(response.json()['id'])
        self.assertEqual(job['status'],'completed',job)
        return job,stream.getvalue(),item

    def test_manifest_comparison_original_and_private_cleanup(self):
        job,raw,item=self.feather_job()
        result=job['result']
        manifest=self.client.get(result['manifest_url']).json()
        self.assertEqual(manifest['canvas']['actual_margins']['left'],64)
        self.assertEqual(result['preservation']['different_pixels'],0)
        self.assertEqual(self.client.get(item['download_url']).content,raw)
        self.assertEqual(self.client.get(result['strict_url']).status_code,200)
        self.assertEqual(self.client.get(result['comparison_url']).status_code,200)
        path=self.app.state.store.file(result['image_id'])
        response=self.client.post(f'/api/images/{result["image_id"]}/encrypt',headers=self.headers,
                                  json={'password':'test-password-123456','confirm_password':'test-password-123456'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse((path.parent/'strict.png').exists())
        self.assertFalse((path.parent/'comparison.png').exists())
        self.assertNotEqual(self.client.get(result['strict_url']).status_code,200)

    def test_failed_save_discards_comparisons(self):
        with patch('app.image_tools.verify_core',side_effect=ValueError('protection failed')):
            original,candidate,_,params=fixture()
            params.update(extend_left=64,extend_right=64,extend_top=64,extend_bottom=64,tone_match=False)
            stream=io.BytesIO(); original.save(stream,'PNG')
            item=self.post('/inputs',files={'file':('x.png',stream.getvalue(),'image/png')}).json()
            with self.available(),patch.object(self.tools,'run_outpaint',return_value=(candidate.resize((256,256)),{})):
                response=self.request(item['id'],'outpaint',params)
                job=self.wait_job(response.json()['id'])
            self.assertEqual(job['status'],'failed')
        self.assertFalse(list((self.root/'outputs').rglob('*.png')))
        self.assertFalse(self.app.state.store.images())

    def test_enhancement_never_claims_verified_or_accepts_fake_job(self):
        caps=self.client.get('/api/image-tools/capabilities').json()['enhance']
        self.assertFalse(caps['available']); self.assertFalse(caps['verified'])
        self.assertEqual(caps['models'],[])
        item=self.upload()
        self.assertEqual(self.request(item['id'],'enhance').status_code,422)
