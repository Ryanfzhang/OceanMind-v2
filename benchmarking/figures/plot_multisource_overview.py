"""Minimal global overview of the benchmark's regional, multisource inputs."""
from pathlib import Path
import os
import json
os.environ.setdefault('MPLCONFIGDIR', '/tmp/oceanx-map-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Rectangle
from matplotlib.offsetbox import AnnotationBbox, TextArea, VPacker
import cartopy.crs as ccrs
import cartopy.feature as cf
from cartopy.mpl.ticker import LongitudeFormatter, LatitudeFormatter

HERE = Path(__file__).resolve().parent
groups = json.loads((HERE.parent/'download/public_manifest.json').read_text())['groups']
# Keep the requested font explicit; fail rather than silently substitute.
font_manager.findfont('Times New Roman', fallback_to_default=False)
plt.rcParams.update({'font.family':'Times New Roman',
                     'svg.fonttype':'none','pdf.fonttype':42,'font.size':14})
fig = plt.figure(figsize=(16,7.35), facecolor='white')
fig.text(.06,.953,'OceanX benchmark',fontsize=28,fontweight='bold',color='#20384B')
fig.text(.94,.957,'MULTI-SOURCE OCEAN DATA',ha='right',fontsize=16,color='#587583')
pc = ccrs.PlateCarree()
ax = fig.add_axes([.06,.135,.88,.765],projection=pc)
ax.set_extent([-180,180,-62,80],crs=pc)
ax.set_facecolor('#F0F6F8')
ax.add_feature(cf.NaturalEarthFeature('physical','land','110m',facecolor='#DFE4E1',edgecolor='none'),zorder=1)
ax.coastlines(resolution='110m',color='#89999F',linewidth=.6,zorder=3)
ax.set_xticks([-180,-120,-60,0,60,120,180],crs=pc)
ax.set_yticks([-60,-30,0,30,60],crs=pc)
ax.xaxis.set_major_formatter(LongitudeFormatter())
ax.yaxis.set_major_formatter(LatitudeFormatter())
ax.tick_params(labelsize=13,colors='#718591',length=0,pad=7)
ax.gridlines(xlocs=[-180,-120,-60,0,60,120,180],ylocs=[-60,-30,0,30,60],
             color='#BDC9CF',linewidth=.45,linestyle=(0,(3,3)),zorder=0)
ax.spines['geo'].set_edgecolor('#B6C3C9')
ax.spines['geo'].set_linewidth(.7)

def region(group, color, title, products, anchor, label):
    w,e,s,n = groups[group]['bbox']
    ax.add_patch(Rectangle((w,s),e-w,n-s,transform=pc,facecolor=color,alpha=.13,edgecolor='none',zorder=2))
    ax.add_patch(Rectangle((w,s),e-w,n-s,transform=pc,facecolor='none',edgecolor=color,lw=2,zorder=4))
    heading = TextArea(title,textprops={'color':color,'fontsize':18,'fontweight':'bold'})
    content = TextArea(products,textprops={'color':'#415C6A','fontsize':14,'linespacing':1.5})
    box = VPacker(children=[heading,content],align='center',pad=3,sep=7)
    note = AnnotationBbox(box,anchor,xybox=label,xycoords='data',boxcoords='data',
        frameon=True,bboxprops={'boxstyle':'round,pad=.5','fc':'white','ec':color,'lw':.9},
        arrowprops={'arrowstyle':'-','color':color,'lw':1.25,'connectionstyle':'arc3,rad=.08'},zorder=7)
    ax.add_artist(note)

region('P_MODIS','#258B82','A  South China Sea',
       'CMOMS / MODIS-Aqua /\nNOAA Blended Sea Winds',(112,12),(65,-26))
region('P_ECS','#B3713F','B  East China Sea',
       'OISST / GLORYS12 / ERA5',(125,32),(136,61))
region('P_GULF','#376B9D','C  Gulf of Mexico',
       'GLORYS12',(-89,25),(-103,58))
for x,y,t in [(-138,-25,'PACIFIC OCEAN'),(-31,0,'ATLANTIC OCEAN')]:
    ax.text(x,y,t,color='#9BAEB8',fontsize=13,ha='center')
fig.text(.5,.063,'Satellite observations  ·  Ocean simulations & reanalysis  ·  Atmospheric reanalysis',
         ha='center',fontsize=16,color='#415C6A')
fig.text(.5,.022,'Boxes show configured regional subsets; CMOMS uses supplied grids and masks.  |  Basemap: Natural Earth',
         ha='center',fontsize=11.5,color='#81919B')
for ext in ['png','svg','pdf']:
    fig.savefig(HERE/f'benchmark_multisource_overview.{ext}',dpi=300,facecolor='white')
plt.close(fig)
