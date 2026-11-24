"""Behavioral boundary checks for the isolated production input extension."""
import copy
import unittest

import torch
from scripts.v2_ops.contract import Vocabulary
from scripts.v2_ops.decoder import StructuredPolicy
from scripts.v2_attention.model import AttentivePolicy, AttentionConfig
from scripts.v2_attention.typed_production import TypedPolicy, candidate_type_id, public_candidate_inputs


class TypedProductionTests(unittest.TestCase):
    def setUp(self):
        self.vocab=Vocabulary(['ABSTRACT_TYPE_INFANTRYTYPE:Same','ABSTRACT_TYPE_UNITTYPE:Same'])
        self.a=dict(name='Same',features=[.1,0.,1.,0.,.2,0.,0.,.1])
        self.b=dict(name='Same',features=[.1,0.,0.,1.,.2,0.,0.,.1])
        self.i=candidate_type_id(self.a,self.vocab.ids);self.j=candidate_type_id(self.b,self.vocab.ids)

    def entity(self,addr,typ=None,**kwargs):
        obj=dict(addr=addr,health=100,on_map=True,in_limbo=False);obj.update(kwargs)
        return dict(friendly=True,type_id=self.i if typ is None else typ,object=obj)

    def test_identity_not_slot_or_name_alone(self):
        self.assertNotEqual(self.i,self.j)
        with self.assertRaisesRegex(ValueError,'unknown production type'):
            candidate_type_id(dict(self.a,name='Unknown'),self.vocab.ids)
        with self.assertRaisesRegex(ValueError,'exactly one'):
            candidate_type_id(dict(self.a,features=[0,0,0,0,0,0,0,0]),self.vocab.ids)

    def test_public_counts_and_candidate_permutation(self):
        es=[self.entity(i) for i in range(40)]
        es += [copy.deepcopy(es[0]),self.entity(40,in_limbo=True),self.entity(41,on_map=False),self.entity(42,health=0),self.entity(43,self.j)]
        enemy=self.entity(44);enemy['friendly']=False;es.append(enemy)
        s=dict(entities=es,production=[self.a,self.b],future='not read',hidden_enemies='not read')
        ids,counts=public_candidate_inputs(s,self.vocab.ids)
        self.assertEqual(ids,[self.i,self.j]);self.assertEqual(counts,[[40/32,2/32],[1/32,0]])
        s['production'].reverse()
        self.assertEqual(public_candidate_inputs(s,self.vocab.ids),(ids[::-1],counts[::-1]))
        # Public-only helper requires neither previous nor future state.
        self.assertEqual(public_candidate_inputs(dict(entities=[],production=[]),self.vocab.ids),([],[]))

    def test_zero_extension_equivalence_and_learning(self):
        config=AttentionConfig(heads=4)
        parent=AttentivePolicy(self.vocab,StructuredPolicy(self.vocab),config)
        typed=TypedPolicy(self.vocab,parent,config)
        s=dict(entities=[self.entity(1)],production=[self.a,self.b])
        f=torch.zeros(1,96,8);f[0,:2]=torch.tensor([self.a['features'],self.b['features']])
        old=parent.encode_production([s],f);new=typed.encode_production([s],f)
        self.assertTrue(torch.equal(old,new))
        new[0,:2].sum().backward()
        self.assertGreater(typed.production_encoder[0].weight.grad[:,8:].abs().sum().item(),0)
        # Once learned, candidate order is equivariant in this encoder.
        with torch.no_grad():typed.production_encoder[0].weight[:,8:].fill_(.01)
        first=typed.encode_production([s],f)
        s['production'].reverse();f[0,:2]=f[0,:2].flip(0)
        second=typed.encode_production([s],f)
        self.assertTrue(torch.equal(first[0,:2],second[0,:2].flip(0)))


if __name__=='__main__':unittest.main()
