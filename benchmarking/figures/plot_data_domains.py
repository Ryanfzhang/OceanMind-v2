"""Plot benchmark domains from the numerical acquisition manifest (no data download)."""
from pathlib import Path
import os
import json
os.environ.setdefault('MPLCONFIGDIR', '/tmp/oceanx-map-mpl')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import cartopy.crs as ccrs
import cartopy.feature as cf
from cartopy.mpl.ticker import LongitudeFormatter, LatitudeFormatter

HERE = Path(__file__).resolve().parent
MANIFEST = HERE.parent / 'download' / 'public_manifest.json'
M = json.loads(MANIFEST.read_text())
G, MASK = M['groups'], M['masks']
OUT = HERE / 'benchmark_data_domains'
COL = {'gulf':'#376B9D', 'scs':'#258B82', 'ecs':'#B3713F', 'cmoms':'#8463A0'}
INK, MUTED = '#20384B', '#617383'
PC = ccrs.PlateCarree()
plt.rcParams.update({'font.family':'sans-serif', 'font.sans-serif':['Arial','DejaVu Sans'],
    'font.size':10, 'text.color':INK, 'axes.labelcolor':INK,
    'svg.fonttype':'none', 'pdf.fonttype':42, 'axes.linewidth':0.6})

def base(rect, extent, xticks, yticks, scale='50m'):
    ax = fig.add_axes(rect, projection=PC)
    ax.set_extent(extent, crs=PC)
    ax.set_facecolor('#F0F6F8')
    ax.add_feature(cf.NaturalEarthFeature('physical','land',scale,
        facecolor='#E1E4E1',edgecolor='none'), zorder=1)
    ax.coastlines(resolution=scale, color='#75858B', linewidth=.55, zorder=3)
    ax.set_xticks(xticks, crs=PC); ax.set_yticks(yticks, crs=PC)
    ax.xaxis.set_major_formatter(LongitudeFormatter())
    ax.yaxis.set_major_formatter(LatitudeFormatter())
    ax.tick_params(labelsize=8, colors=MUTED, length=0, pad=5)
    ax.gridlines(xlocs=xticks, ylocs=yticks, color='#B8C8CF', linewidth=.4,
        linestyle=(0,(3,3)), zorder=0)
    ax.spines['geo'].set_edgecolor('#A9B7BD')
    return ax

def box(ax,b,color,ls='-',fill=True,lw=1.8):
    w,e,s,n=b
    if fill:
        ax.add_patch(Rectangle((w,s),e-w,n-s,facecolor=color,alpha=.10,
            edgecolor='none',transform=PC,zorder=2))
    ax.add_patch(Rectangle((w,s),e-w,n-s,facecolor='none',edgecolor=color,
        linewidth=lw,linestyle=ls,transform=PC,zorder=5))

def tag(ax,xy,text,color,xytext):
    ax.annotate(text,xy=xy,xytext=xytext,xycoords='data',textcoords='data',
        fontsize=10,fontweight='bold',color=color,ha='center',va='center',
        bbox=dict(boxstyle='round,pad=.4',facecolor='white',edgecolor=color,lw=.8),
        arrowprops=dict(arrowstyle='-',color=color,lw=1,connectionstyle='angle3'),zorder=8)

fig=plt.figure(figsize=(16,11),facecolor='white')
fig.text(.055,.959,'OceanX benchmark | Data geography',fontsize=24,fontweight='bold')
fig.text(.055,.923,'Three regional clusters · six acquisition groups · CMOMS supplied separately',
    fontsize=12,color=MUTED)

world=base([.055,.595,.89,.292],[-180,180,-62,80],[-180,-120,-60,0,60,120,180],[-60,-30,0,30,60],'110m')
for key,gid in [('gulf','P_GULF'),('scs','P_MODIS'),('ecs','P_ECS')]:
    box(world,G[gid]['bbox'],COL[key],lw=1.7)
tag(world,(-89,25),'A  Gulf of Mexico',COL['gulf'],(-100,58))
tag(world,(112,12),'B  South China Sea',COL['scs'],(65,-25))
tag(world,(124,30),'C  East China Sea',COL['ecs'],(145,59))
world.text(-157,-37,'PACIFIC OCEAN',fontsize=9,color='#95AAB4',ha='center')
world.text(-34,6,'ATLANTIC',fontsize=9,color='#95AAB4',ha='center')
world.text(72,-5,'INDIAN OCEAN',fontsize=9,color='#95AAB4',ha='center')

xs=[.055,.365,.675]; width=.27
titles=['A  Gulf of Mexico','B  South China Sea','C  East China Sea']
for x,title,key in zip(xs,titles,['gulf','scs','ecs']):
    fig.text(x,.556,title,fontsize=15,fontweight='bold',color=COL[key])

a=base([xs[0],.298,width,.233],[-102,-77,15,34],[-100,-95,-90,-85,-80],[15,20,25,30])
box(a,G['P_GULF']['bbox'],COL['gulf'])
box(a,MASK['gulf_analysis'],COL['gulf'],'--',False,1.2)
a.plot([-90,-90],[18,30],color=COL['gulf'],ls=':',lw=1,transform=PC,zorder=5)
box(a,[-92,-88,23,27],COL['gulf'],':',False,1.1)
a.text(-89,25,'Q17',ha='center',fontsize=8,color=COL['gulf'],zorder=6)
a.text(-96,32,'UNITED STATES',fontsize=8,color=MUTED)
a.text(-100,20,'MEXICO',fontsize=8,color=MUTED,rotation=45)

b=base([xs[1],.298,width,.233],[102,124,-1,27],[105,110,115,120],[0,5,10,15,20,25])
box(b,G['P_MODIS']['bbox'],COL['scs'])
for key in ['scs_coastal_box','scs_offshore_box','seats_box','vietnam_box']:
    box(b,MASK[key],COL['scs'],'--',False,1.0)
b.text(111.8,23.5,'Coastal',color=COL['scs'],fontsize=8,zorder=6)
b.text(118.2,18.4,'Offshore',color=COL['scs'],fontsize=8,zorder=6)
b.text(108.8,12.7,'Vietnam',color=COL['scs'],fontsize=8,ha='right',zorder=6)
b.annotate('SEATS',xy=(116,18),xytext=(110,17),fontsize=7.5,color=COL['scs'],
    arrowprops=dict(arrowstyle='-',color=COL['scs'],lw=.6),zorder=6)
b.plot(113.5,22.2,marker='*',ms=10,mec='white',mew=.6,color=COL['cmoms'],transform=PC,zorder=7)
b.text(108,25.5,'CHINA',fontsize=8,color=MUTED)

c=base([xs[2],.298,width,.233],[116,132,21,38],[116,120,124,128,132],[22,26,30,34,38])
box(c,G['P_ECS']['bbox'],COL['ecs'])
box(c,MASK['ecs_analysis'],COL['ecs'],'--',False,1.4)
c.text(124,29.5,'OISST\nanalysis domain',fontsize=9,ha='center',color=COL['ecs'],zorder=6)
c.text(120,36.2,'CHINA',fontsize=8,color=MUTED)
c.text(126.5,36,'KOREA',fontsize=8,color=MUTED)
c.text(120,22.4,'TAIWAN',fontsize=7,color=MUTED,rotation=60)

def info(x,heading,lines):
    fig.text(x,.267,heading,fontsize=10.5,fontweight='bold')
    for i,line in enumerate(lines):
        fig.text(x,.242-i*.022,line,fontsize=9.2,color=MUTED)

info(xs[0],'GLORYS12 / CMEMS',[
    '2011–2017 · daily · temperature, salinity, sea level',
    '98–80°W, 18–31°N · 0–2000 m',
    'Analysis: 98–82°W, 18–30°N',
    'Q13–Q18, Q29–Q30  |  8 tasks'])
info(xs[1],'MODIS-Aqua + NOAA Blended Sea Winds',[
    'Chlorophyll: 2003–2017 · monthly',
    'Wind: 2003–2012 · monthly',
    '104–122°E, 1–25°N',
    'Q19–Q22, Q28  |  5 tasks'])
info(xs[2],'OISST + GLORYS12 + ERA5',[
    'SST: 1982–2023 · daily',
    'GLORYS / ERA5: 1993–2011 + 2023 · May–Nov',
    '119–129°E, 24–35°N; inner box: 120–128°E, 25–34°N',
    'Q23–Q24  |  2 tasks'])

fig.add_artist(plt.Line2D([.055,.945],[.133,.133],transform=fig.transFigure,color='#D7E0E3',lw=.8))
fig.text(.055,.106,'CMOMS',fontsize=10,fontweight='bold',color=COL['cmoms'])
fig.text(.12,.106,'South China Sea basin, shelf and Pearl River estuary · Q01–Q12, Q25–Q27 (15 tasks)',fontsize=10)
fig.text(.055,.082,'CMOMS extents follow supplied grids and masks; no exact boundary is inferred. The purple star locates the Pearl River estuary only.',fontsize=9,color=MUTED)
fig.text(.055,.047,'Solid outline: acquisition domain     Dashed outline: analysis box     Dotted lines: Gulf split / Q17 subregion',fontsize=9,color=MUTED)
fig.text(.055,.024,'Source: benchmark manifest '+M['version']+' · Boundaries: Natural Earth · Required coverage, not download-completion status',fontsize=8,color=MUTED)

for ext in ['png','svg','pdf']:
    fig.savefig(OUT.with_suffix('.'+ext),dpi=250,facecolor='white')
plt.close(fig)
source={'manifest_version':M['version'],'groups':G,'masks':MASK,
    'additional_analysis_box':{'Q17':[-92,-88,23,27]},
    'cmoms':{'exact_domain':None,'locator_only_PRE':[113.5,22.2]},
    'note':'Required benchmark geography, not verified download coverage. CMOMS exact masks are not available here.'}
OUT.with_suffix('.json').write_text(json.dumps(source,indent=2)+'\n')
print(OUT)
