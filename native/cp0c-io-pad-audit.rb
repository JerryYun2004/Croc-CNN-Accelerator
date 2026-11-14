# Permit only polarity-preserving parallel tap/diode reduction.
module IOPadReduction
  def self.check(ok,msg);raise 'Pad reduction audit: '+msg unless ok;end
  def self.close(a,b);(a-b).abs<=1e-8;end
  def self.groups(inv)
    groups={}
    inv.fetch('devices').each do |d|
      model=d.fetch('model').downcase;p=d.fetch('parameters');t=d.fetch('terminals')
      if model=='ptap1'
        check(p.keys.sort==%w[A P] && t.keys.sort==%w[TIE WELL],'tap interface changed')
        key=[model,t['TIE'],t['WELL']]
      elsif %w[dantenna dpantenna].include?(model)
        check(p.keys.sort==%w[A P m] && t.keys.sort==%w[A C],'diode interface changed')
        key=[model,t['A'],t['C'],p['A'].round(8),p['P'].round(8)]
      else
        next
      end
      (groups[key] ||= []) << d
    end
    groups
  end
  def self.verify(pre,post)
    check(pre['nets'].uniq.size==pre['nets'].size,'ambiguous physical net names')
    check(pre['nets']==post['nets'] && pre['pins']==post['pins'],'nets or boundary pins changed')
    fixed=lambda { |inv| inv['devices'].reject { |d| %w[ptap1 dantenna dpantenna].include?(d['model'].downcase) }.sort_by { |d| d['name'] } }
    check(fixed.call(pre)==fixed.call(post),'MOS/resistor changed during reduction')
    a=groups(pre);b=groups(post);check(a.keys.sort==b.keys.sort,'parallel endpoints or diode A/P changed')
    rows=[]
    a.each do |key,ds|
      check(b[key].size==1,'parallel group did not reduce to one device')
      p=b[key].first['parameters'];model=key.first
      if model=='ptap1'
        %w[A P].each { |param| check(close(p[param],ds.sum { |d| d['parameters'][param] }),'tap A/P sum differs') }
      else
        check(close(p['m'],ds.sum { |d| d['parameters']['m'] }),'diode multiplicity differs')
        %w[A P].each { |param| check(ds.all? { |d| close(d['parameters'][param],p[param]) },'diode geometry changed') }
      end
      rows << {group:key,before:ds.size,after:1,parameters:p}
    end
    {status:'ONLY_PARALLEL_TAPS_AND_DIODES_REDUCED',groups:rows,
     fixed_devices_unchanged:true,pins_unchanged:true,nets_unchanged:true,net_joins:0}
  end
end
