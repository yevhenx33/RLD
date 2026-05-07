import os, requests
from eth_utils import keccak
RPC=os.environ['MAINNET_RPC_URL']
def sel(sig): return '0x'+keccak(text=sig)[:4].hex()
def call(to,data):
 r=requests.post(RPC,json={'jsonrpc':'2.0','id':1,'method':'eth_call','params':[{'to':to,'data':data},'latest']},timeout=60).json()
 return r
for name,addr,calls in [
 ('vault56','0x56ddF84B2c94BF3361862FcEdB704C382dc4cd32',['getAllVaultsAddresses()','getVaultEntireData(address)']),
 ('vault814','0x814c8C7ceb1411B364c2940c4b9380e739e06686',['getAllVaultsAddresses()','getVaultEntireData(address)']),
 ('dex717','0x71783F64719899319B56BdA4F27E1219d9AF9a3d',['getAllDexesAddresses()','getDexEntireData(address)']),
]:
 print('---',name,addr)
 for sig in calls:
  data=sel(sig)
  if '(address)' in sig: data += '0'*24+'080574d224e960c272e005aa03efbe793f317640' # sample dex/vault-ish
  res=call(addr,data)
  print(sig, res.get('result','')[:80], res.get('error'))
