import json, os, pathlib, random, socket, subprocess, time, statistics
ROOT=pathlib.Path(__file__).resolve().parents[1]
out=ROOT/'lab-output'/'consumer-scheduling';out.mkdir(parents=True,exist_ok=True)
random.seed(17)
records=[]
for rate in (1000,5000):
    for repeat in range(3):
        modes=['sleep','spin'];random.shuffle(modes)
        for mode in modes:
            with socket.socket() as s:
                s.bind(('127.0.0.1',0));port=s.getsockname()[1]
            cmd=[str(ROOT/'build'/'udp_pipeline'),'-',str(port),'600','9000','1024','0','unused.jsonl','--rx=batch','--sink=none',f'--idle={mode}']
            recv=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                time.sleep(.10)
                pub=subprocess.run([str(ROOT/'build'/'udp_publisher'),str(port),'600',str(rate)],capture_output=True,text=True,timeout=12)
                stdout,stderr=recv.communicate(timeout=12)
                item={'rate_target':rate,'repeat':repeat,'mode':mode,'publisher_returncode':pub.returncode,'receiver_returncode':recv.returncode}
                if stdout.strip():item['receiver']=json.loads(stdout.strip().splitlines()[-1])
                if pub.stdout.strip():item['publisher']=json.loads(pub.stdout.strip().splitlines()[-1])
                if stderr.strip():item['stderr']=stderr
                if pub.stderr.strip():item['publisher_stderr']=pub.stderr
                records.append(item)
                print(rate,repeat,mode,item.get('receiver',{}).get('consumer_thread_cpu_ns'),item.get('receiver',{}).get('consumer_voluntary_context_switches'),item.get('receiver',{}).get('queue_p99_us'),flush=True)
            finally:
                if recv.poll() is None:recv.kill();recv.wait()
(out/'trials.json').write_text(json.dumps(records,indent=2)+'\n')
summary=[]
for rate in (1000,5000):
    for mode in ('sleep','spin'):
        subset=[x for x in records if x['rate_target']==rate and x['mode']==mode]
        passed=[x for x in subset if x['receiver_returncode']==0 and x['publisher_returncode']==0 and x['receiver']['received_datagrams']==600 and x['receiver']['application_queue_drops']==0 and x['receiver']['complete']]
        summary.append({'target_pps':rate,'mode':mode,'total':len(subset),'integrity_passed':len(passed),'worker_cpu_ms_median':statistics.median(x['receiver']['consumer_thread_cpu_ns']/1e6 for x in passed) if passed else None,'worker_voluntary_context_switches_median':statistics.median(x['receiver']['consumer_voluntary_context_switches'] for x in passed) if passed else None,'worker_involuntary_context_switches_median':statistics.median(x['receiver']['consumer_involuntary_context_switches'] for x in passed) if passed else None,'queue_p99_us_median':statistics.median(x['receiver']['queue_p99_us'] for x in passed) if passed else None})
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print('SUMMARY',json.dumps(summary,indent=2))
