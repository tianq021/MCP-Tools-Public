"""Exactly one executable WDZS-adapted teacher demonstration, no neural training."""
import argparse
import asyncio
import json
import pathlib
import sys
import time
import traceback

ROOT=pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
from dsh_rl.full_env import RA2FullEnv
from dsh_rl.env import RA2MicroEnv
from ai_teacher.wdzs_teacher import WdzsTeacher,VERSION

class TeacherEnvironment(RA2FullEnv):
    def _control_metadata(self):
        return VERSION,{"control_mode":"existing_raw_bridge_teacher","student_301_contract_unchanged":True,
            "learning_eligible":False,"reason":"raw teacher trace, no 301 label conversion or BC acceptance"}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--out",required=True);p.add_argument("--game-dir",required=True)
    p.add_argument("--bundle",required=True);p.add_argument("--seconds",type=float,default=600)
    p.add_argument("--port",type=int,default=13361);p.add_argument("--preflight-only",action="store_true")
    a=p.parse_args();out=pathlib.Path(a.out);out.mkdir(parents=True,exist_ok=True)
    RA2MicroEnv.RESET_RETRY_DELAYS=()
    teacher=WdzsTeacher(a.bundle)
    env=TeacherEnvironment(out/"episodes",max_seconds=a.seconds,decision_seconds=0.5,
        game_dir=a.game_dir,port=a.port,kill_stale=False,protection={"enabled":True,
        "stall_timeout_s":5,"hard_step_timeout_s":30,"screenshot_interval_s":30})
    if a.preflight_only:
        env.loop.close();print("Teacher/environment construction PASS; game launches=0");return
    result={"teacher":VERSION,"learning_eligible":False,"training_started":False,"student_model_loaded":False,
        "raw_teacher_steps":0,"raw_intents":0,"no_301_labels_generated":True,"status":"STARTING"}
    def persist():(out/"TEACHER_RESULT.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    async def execute_and_observe(plan):
        env._dec_acks=[]
        invalid=await env._commands(plan["intents"],"wdzs_script_teacher")
        acks=list(env._dec_acks)
        await asyncio.sleep(0.5)
        await env._capture()
        return invalid,acks
    try:
        env.reset();result["episode_id"]=env.writer.episode_id
        result["game_pid"]=env.game_pid;persist()
        with (out/"teacher-decisions.jsonl").open("x",encoding="utf-8") as trace:
            while env.kind=="running":
                before=env.current
                plan=teacher.plan(before,env.full,env.starts,env.map_soa)
                invalid,acks=env._run(execute_and_observe(plan),name="teacher_commands_and_observation")
                record={"before_frame":before["frame"],"after_frame":env.current["frame"],
                    "t":before["t"],"plan":plan,"ack_records":acks,"invalid":invalid,
                    "student_301_label":None,"learning_eligible":False}
                trace.write(json.dumps(record,ensure_ascii=False)+"\n");trace.flush()
                result["raw_teacher_steps"]+=1;result["raw_intents"]+=len(plan["intents"])
                if result["raw_teacher_steps"]%30==0:
                    b=[o["type_name"] for o in env.current["own_objects"] if o.get("abstract")=="ABSTRACT_TYPE_BUILDING" and o.get("on_map") and not o.get("in_limbo")]
                    print(json.dumps({"t":env.current["t"],"phase":plan["phase"],"team":plan.get("team",{}).get("name") if plan.get("team") else None,"buildings":b,"money":env.current["money"]},ensure_ascii=False),flush=True)
                    persist()
        result["status"]="FINISHED";result["end_kind"]=env.kind
        result["elapsed_s"]=env.current["t"]
        result["final_own_types"]=dict(__import__('collections').Counter(o["type_name"] for o in env.current["own_objects"] if o.get("on_map") and not o.get("in_limbo") and o.get("health",0)>0))
        env._finish(env.kind)
    except BaseException as exc:
        result.update(status="FAILED",error=f"{type(exc).__name__}: {exc}",traceback=traceback.format_exc())
        persist();raise
    finally:
        env.close();persist()
        (out/"EXCLUDED_FROM_TRAINING.json").write_text(json.dumps({"learning_eligible":False,
            "reason":"Raw teacher demonstration; no approved conversion to student's 301 action labels."}),encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=="__main__":main()
