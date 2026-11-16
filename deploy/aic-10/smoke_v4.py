import asyncio, httpx
from compare_v4 import trial

async def main():
    async with httpx.AsyncClient(trust_env=False) as c:
        for p in ('round_robin', 'v4_eval', 'v4_sched'):
            r = await trial(c, '도서관의 역할을 한 문장으로 설명해 줘.', p, 18300)
            print(p, {k: r[k] for k in ('server', 'predicted', 'original', 'output', 'first', 'finish')})
asyncio.run(main())
