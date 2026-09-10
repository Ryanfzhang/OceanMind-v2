"""Regional benchmark map with a compact product/provenance sidebar."""
from pathlib import Path
import json
import os
os.environ.setdefault('MPLCONFIGDIR','/tmp/oceanx-map-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch
from PIL import Image
import cartopy.crs as ccrs
import cartopy.feature as cf
from cartopy.mpl.ticker import LongitudeFormatter, LatitudeFormatter

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent.parent
GROUPS=json.loads((HERE.parent/'download/public_manifest.json').read_text())['groups']
COL={'A':'#258B82','B':'#B3713F','C':'#376B9D'}
INK='#243B4B'; MUTED='#72838E'
plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Arial','DejaVu Sans'],
    'svg.fonttype':'none','pdf.fonttype':42,'font.size':10})
fig=plt.figure(figsize=(17,7.2),facecolor='white')
fig.text(.044,.924,'OceanX benchmark',fontsize=23,fontweight='bold',color=INK)
fig.text(.044,.877,'Regional coverage and multi-source data',fontsize=12,color=MUTED)
pc=ccrs.PlateCarree()
ax=fig.add_axes([.044,.175,.635,.655],projection=pc)
ax.set_extent([-180,180,-62,80],crs=pc)
ax.set_facecolor('#F1F6F8')
ax.add_feature(cf.NaturalEarthFeature('physical','land','110m',facecolor='#E1E5E2',edgecolor='none'),zorder=1)
ax.coastlines(resolution='110m',color='#8C9AA0',linewidth=.5,zorder=3)
xt=[-180,-120,-60,0,60,120,180]; yt=[-60,-30,0,30,60]
ax.set_xticks(xt,crs=pc);ax.set_yticks(yt,crs=pc)
ax.xaxis.set_major_formatter(LongitudeFormatter());ax.yaxis.set_major_formatter(LatitudeFormatter())
ax.tick_params(labelsize=8,colors=MUTED,length=0,pad=6)
ax.gridlines(xlocs=xt,ylocs=yt,color='#C2CDD1',linewidth=.4,linestyle=(0,(3,3)),zorder=0)
ax.spines['geo'].set_edgecolor('#B8C5CC');ax.spines['geo'].set_linewidth(.6)
def region(gid,key,title,anchor,label):
    w,e,s,n=GROUPS[gid]['bbox']; color=COL[key]
    ax.add_patch(Rectangle((w,s),e-w,n-s,transform=pc,facecolor=color,alpha=.13,edgecolor='none',zorder=2))
    ax.add_patch(Rectangle((w,s),e-w,n-s,transform=pc,facecolor='none',edgecolor=color,lw=1.8,zorder=5))
    ax.annotate(f'{key}  {title}',xy=anchor,xytext=label,fontsize=10.5,color=color,fontweight='bold',
        ha='center',va='center',bbox={'boxstyle':'round,pad=.5','fc':'white','ec':color,'lw':.8},
        arrowprops={'arrowstyle':'-','color':color,'lw':1.1,'connectionstyle':'arc3,rad=.06'},zorder=7)
region('P_MODIS','A','South China Sea',(112,12),(64,-27))
region('P_ECS','B','East China Sea',(124,31),(134,62))
region('P_GULF','C','Gulf of Mexico',(-89,25),(-101,59))
for x,y,t in [(-140,-25,'PACIFIC OCEAN'),(-29,4,'ATLANTIC')]:
    ax.text(x,y,t,fontsize=8,color='#A0B0B8',ha='center')

fig.add_artist(plt.Line2D([.708,.708],[.145,.852],transform=fig.transFigure,color='#D9E1E5',lw=.9))
fig.text(.734,.916,'DATA SOURCES',fontsize=13,fontweight='bold',color=INK)
fig.text(.734,.878,'6 product families · complementary evidence',fontsize=10,color=MUTED)

def icon(filename,x,y,w=.042):
    # Preserve the approved raster icons; map geometry and text stay vector/editable.
    im=Image.open(ROOT/'output/data-category-icons'/filename)
    ia=fig.add_axes([x,y,w,w*17/7.2]);ia.imshow(im);ia.set_axis_off()

icon('simulation-and-reanalysis.png',.728,.757)
fig.text(.779,.816,'Simulations & Reanalysis',fontsize=13,fontweight='bold',color=INK)

def badge(x,y,key):
    fig.add_artist(FancyBboxPatch((x,y-.004),.017,.027,boxstyle='round,pad=0.002,rounding_size=0.004',
        transform=fig.transFigure,facecolor=COL[key],edgecolor='none'))
    fig.text(x+.0085,y+.008,key,fontsize=8.7,color='white',fontweight='bold',ha='center',va='center')
def row(y,product,institution,regions):
    fig.text(.744,y,product,fontsize=12,fontweight='bold',color=INK)
    fig.text(.744,y-.028,institution,fontsize=9.4,color=MUTED)
    start=.958-.022*(len(regions)-1)
    for i,k in enumerate(regions):badge(start+.022*i,y-.003,k)

row(.744,'CMOMS','HKUST / ODMP','A')
row(.666,'GLORYS12','Mercator Ocean / Copernicus Marine','BC')
row(.588,'ERA5','ECMWF / C3S','B')
fig.add_artist(plt.Line2D([.734,.978],[.531,.531],transform=fig.transFigure,color='#E2E8EB',lw=.8))
icon('satellite-observations.png',.728,.421)
fig.text(.779,.487,'Satellite-derived &',fontsize=13,fontweight='bold',color=INK)
fig.text(.779,.456,'Blended Observations',fontsize=13,fontweight='bold',color=INK)
row(.391,'MODIS-Aqua','NASA / OBPG','A')
row(.313,'Blended Sea Winds','NOAA / NCEI','A')
row(.235,'OISST','NOAA / NCEI','B')
fig.text(.734,.151,'A / B / C tags link products to map regions.',fontsize=9,color=MUTED)
fig.text(.044,.102,'Regional boxes follow the download manifest; CMOMS uses supplied grids and masks.',fontsize=9,color=MUTED)
fig.text(.044,.066,'Basemap: Natural Earth',fontsize=8,color='#93A1AA')
for ext in ['png','svg','pdf']:
    fig.savefig(HERE/f'benchmark_map_sources.{ext}',dpi=300,facecolor='white')
plt.close(fig)
